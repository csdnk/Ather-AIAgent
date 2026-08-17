use ae_common::{AetherError, BlockId, MigrationTaskId, Result, SegmentId};
use ae_kernel::{AetherEngine, MigrationAck, SegmentControl, SegmentMigrationStatus};
use ae_wal::{FileWal, SharedWal};
use e1_vector::{CollectionSpec, InMemoryVectorEngine, VectorRecord};
use e2_object::{LocalObjectEngine, ObjectMeta};
use e3_graph::InMemoryGraphEngine;
use std::env;
use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::Arc;
use tonic::{Request, Response, Status};

pub mod api {
    tonic::include_proto!("aether.engine.v1");
}

use api::object_service_server::{ObjectService, ObjectServiceServer};
use api::segment_control_service_server::{SegmentControlService, SegmentControlServiceServer};
use api::vector_service_server::{VectorService, VectorServiceServer};

#[derive(Clone)]
struct P2Grpc {
    vector: Arc<InMemoryVectorEngine>,
    object: Arc<LocalObjectEngine>,
    graph: Arc<InMemoryGraphEngine>,
}

impl P2Grpc {
    fn new(data_dir: PathBuf) -> Result<Self> {
        std::fs::create_dir_all(&data_dir)?;
        let wal: SharedWal = Arc::new(FileWal::open(data_dir.join("aether.wal"))?);
        let vector = Arc::new(InMemoryVectorEngine::new("default", wal.clone()));
        let object = Arc::new(LocalObjectEngine::new_with_sqlite(
            "default",
            &data_dir,
            wal.clone(),
        )?);
        let graph = Arc::new(InMemoryGraphEngine::new("default", wal));

        vector.recover()?;
        object.recover()?;
        graph.recover()?;

        Ok(Self {
            vector,
            object,
            graph,
        })
    }

    fn controls(&self, engine: &str) -> Result<Vec<(String, Arc<dyn SegmentControl>)>> {
        let vector: Arc<dyn SegmentControl> = self.vector.clone();
        let object: Arc<dyn SegmentControl> = self.object.clone();
        let graph: Arc<dyn SegmentControl> = self.graph.clone();
        let normalized = engine.trim().to_ascii_lowercase().replace(':', "/");
        match normalized.as_str() {
            "" => Ok(vec![
                ("vector/default".into(), vector),
                ("object/default".into(), object),
                ("graph/default".into(), graph),
            ]),
            "vector" | "vector/default" | "e1" | "e1/default" => {
                Ok(vec![("vector/default".into(), vector)])
            }
            "object" | "object/default" | "e2" | "e2/default" => {
                Ok(vec![("object/default".into(), object)])
            }
            "graph" | "graph/default" | "e3" | "e3/default" => {
                Ok(vec![("graph/default".into(), graph)])
            }
            _ => Err(AetherError::InvalidArgument(format!(
                "unknown engine scope {engine}"
            ))),
        }
    }

    fn control_for_segment(
        &self,
        engine: &str,
        segment: &SegmentId,
    ) -> Result<(String, Arc<dyn SegmentControl>)> {
        if !engine.trim().is_empty() {
            return self
                .controls(engine)?
                .into_iter()
                .next()
                .ok_or_else(|| AetherError::InvalidArgument("engine scope is empty".into()));
        }

        let mut matches = Vec::new();
        for (name, control) in self.controls("")? {
            match control.migration_status(segment) {
                Ok(_) => matches.push((name, control)),
                Err(AetherError::NotFound(_)) => {}
                Err(error) => return Err(error),
            }
        }
        match matches.len() {
            0 => Err(AetherError::NotFound(format!("segment {}", segment.0))),
            1 => Ok(matches.remove(0)),
            _ => Err(AetherError::Conflict(format!(
                "segment {} exists in multiple engines; engine scope is required",
                segment.0
            ))),
        }
    }
}

fn status(error: AetherError) -> Status {
    match error {
        AetherError::NotFound(message) => Status::not_found(message),
        AetherError::AlreadyExists(message) => Status::already_exists(message),
        AetherError::Conflict(message) => Status::aborted(message),
        AetherError::InvalidArgument(message) | AetherError::Unsupported(message) => {
            Status::invalid_argument(message)
        }
        AetherError::Timeout(message) => Status::deadline_exceeded(message),
        AetherError::QuotaExceeded(message) => Status::resource_exhausted(message),
        AetherError::SegmentFrozen(message) => Status::failed_precondition(message),
        other => Status::internal(other.to_string()),
    }
}

fn object_meta(meta: ObjectMeta) -> api::ObjectMeta {
    api::ObjectMeta {
        bucket: meta.bucket,
        key: meta.key,
        etag: meta.etag,
        size: meta.size,
        md5_hex: meta.md5_hex,
        blake3_hex: meta.blake3_hex,
    }
}

fn segment_info(engine: &str, status: SegmentMigrationStatus) -> api::SegmentInfo {
    api::SegmentInfo {
        engine: engine.into(),
        segment_id: status.segment_id.0,
        state: status.state,
        route_epoch: status.migration.route_epoch,
        block_ids: status
            .migration
            .block_ids
            .into_iter()
            .map(|block| block.0)
            .collect(),
        active_migration_id: status
            .migration
            .active_migration_id
            .map(|migration| migration.0),
        last_migration_id: status
            .migration
            .last_migration_id
            .map(|migration| migration.0),
        last_outcome: status
            .migration
            .last_outcome
            .map(|outcome| outcome.as_str().to_string()),
    }
}

fn control_ack(engine: &str, ack: MigrationAck) -> api::Ack {
    let migration_id = ack
        .status
        .migration
        .active_migration_id
        .as_ref()
        .or(ack.status.migration.last_migration_id.as_ref())
        .map(|migration| migration.0.clone())
        .unwrap_or_default();
    api::Ack {
        ok: true,
        engine: engine.into(),
        segment_id: ack.status.segment_id.0,
        migration_id,
        route_epoch: ack.status.migration.route_epoch,
        idempotent: ack.idempotent,
        state: ack.status.state,
        block_ids: ack
            .status
            .migration
            .block_ids
            .into_iter()
            .map(|block| block.0)
            .collect(),
        outcome: ack
            .status
            .migration
            .last_outcome
            .map(|outcome| outcome.as_str().to_string()),
    }
}

#[tonic::async_trait]
impl VectorService for P2Grpc {
    async fn create_collection(
        &self,
        request: Request<api::CreateCollectionRequest>,
    ) -> std::result::Result<Response<api::CreateCollectionResponse>, Status> {
        let request = request.into_inner();
        self.vector
            .create_collection(CollectionSpec {
                name: request.collection,
                dimension: request.dimension as usize,
            })
            .map_err(status)?;
        Ok(Response::new(api::CreateCollectionResponse { ok: true }))
    }

    async fn insert_vector(
        &self,
        request: Request<api::InsertVectorRequest>,
    ) -> std::result::Result<Response<api::InsertVectorResponse>, Status> {
        let request = request.into_inner();
        let records = request
            .records
            .into_iter()
            .map(|record| VectorRecord {
                id: record.id,
                values: record.values,
                graph_node_id: record.graph_node_id,
                metadata_json: record.metadata_json,
            })
            .collect();
        let inserted = self
            .vector
            .insert(&request.collection, records)
            .map_err(status)?;
        Ok(Response::new(api::InsertVectorResponse {
            inserted: inserted as u64,
        }))
    }

    async fn delete_vectors(
        &self,
        request: Request<api::DeleteVectorsRequest>,
    ) -> std::result::Result<Response<api::DeleteVectorsResponse>, Status> {
        let request = request.into_inner();
        let deleted = self
            .vector
            .delete(&request.collection, request.ids)
            .map_err(status)?;
        Ok(Response::new(api::DeleteVectorsResponse {
            deleted: deleted as u64,
        }))
    }

    async fn search_vector(
        &self,
        request: Request<api::SearchVectorRequest>,
    ) -> std::result::Result<Response<api::SearchVectorResponse>, Status> {
        let request = request.into_inner();
        let hits = self
            .vector
            .search(&request.collection, &request.query, request.top_k as usize)
            .map_err(status)?
            .into_iter()
            .map(|hit| api::SearchHit {
                id: hit.id,
                score: hit.score,
                graph_node_id: hit.graph_node_id,
                metadata_json: hit.metadata_json,
            })
            .collect();
        Ok(Response::new(api::SearchVectorResponse { hits }))
    }

    async fn segment_stats(
        &self,
        request: Request<api::SegmentStatsRequest>,
    ) -> std::result::Result<Response<api::SegmentStatsResponse>, Status> {
        let stats = self
            .vector
            .segment_stats(&request.into_inner().collection)
            .map_err(status)?
            .into_iter()
            .map(|stat| api::SegmentStat {
                segment_id: stat.segment_id,
                state: stat.state.as_str().to_string(),
                row_count: stat.row_count as u64,
                size_bytes: stat.size_bytes,
                index_type: stat.index_type,
                access_count: stat.access_count,
            })
            .collect();
        Ok(Response::new(api::SegmentStatsResponse { segments: stats }))
    }
}

#[tonic::async_trait]
impl ObjectService for P2Grpc {
    async fn create_bucket(
        &self,
        request: Request<api::CreateBucketRequest>,
    ) -> std::result::Result<Response<api::ObjectMeta>, Status> {
        let bucket = request.into_inner().bucket;
        self.object.create_bucket(&bucket).map_err(status)?;
        Ok(Response::new(api::ObjectMeta {
            bucket,
            key: String::new(),
            etag: String::new(),
            size: 0,
            md5_hex: None,
            blake3_hex: None,
        }))
    }

    async fn put_object(
        &self,
        request: Request<api::PutObjectRequest>,
    ) -> std::result::Result<Response<api::ObjectMeta>, Status> {
        let request = request.into_inner();
        let meta = self
            .object
            .put_object(&request.bucket, &request.key, &request.data)
            .map_err(status)?;
        Ok(Response::new(object_meta(meta)))
    }

    async fn get_object(
        &self,
        request: Request<api::GetObjectRequest>,
    ) -> std::result::Result<Response<api::ObjectBytes>, Status> {
        let request = request.into_inner();
        let meta = self
            .object
            .head_object(&request.bucket, &request.key)
            .map_err(status)?;
        let data = self
            .object
            .get_object(&request.bucket, &request.key)
            .map_err(status)?;
        Ok(Response::new(api::ObjectBytes {
            data,
            meta: Some(object_meta(meta)),
        }))
    }

    async fn get_object_range(
        &self,
        request: Request<api::GetObjectRangeRequest>,
    ) -> std::result::Result<Response<api::ObjectBytes>, Status> {
        let request = request.into_inner();
        let meta = self
            .object
            .head_object(&request.bucket, &request.key)
            .map_err(status)?;
        let data = self
            .object
            .get_object_range(&request.bucket, &request.key, request.start, request.end)
            .map_err(status)?;
        Ok(Response::new(api::ObjectBytes {
            data,
            meta: Some(object_meta(meta)),
        }))
    }

    async fn head_object(
        &self,
        request: Request<api::GetObjectRequest>,
    ) -> std::result::Result<Response<api::ObjectMeta>, Status> {
        let request = request.into_inner();
        Ok(Response::new(object_meta(
            self.object
                .head_object(&request.bucket, &request.key)
                .map_err(status)?,
        )))
    }

    async fn delete_object(
        &self,
        request: Request<api::GetObjectRequest>,
    ) -> std::result::Result<Response<api::ObjectMeta>, Status> {
        let request = request.into_inner();
        let meta = self
            .object
            .head_object(&request.bucket, &request.key)
            .map_err(status)?;
        self.object
            .delete_object(&request.bucket, &request.key)
            .map_err(status)?;
        Ok(Response::new(object_meta(meta)))
    }

    async fn list_objects(
        &self,
        request: Request<api::ListObjectsRequest>,
    ) -> std::result::Result<Response<api::ListObjectsResponse>, Status> {
        let request = request.into_inner();
        let objects = self
            .object
            .list_objects(&request.bucket, &request.prefix)
            .map_err(status)?
            .into_iter()
            .map(object_meta)
            .collect();
        Ok(Response::new(api::ListObjectsResponse { objects }))
    }

    async fn list_objects_paged(
        &self,
        request: Request<api::ListObjectsPagedRequest>,
    ) -> std::result::Result<Response<api::ListObjectsPagedResponse>, Status> {
        let request = request.into_inner();
        let page = self
            .object
            .list_objects_paged(
                &request.bucket,
                &request.prefix,
                request.max_keys as usize,
                request.continuation_token.as_deref(),
            )
            .map_err(status)?;
        Ok(Response::new(api::ListObjectsPagedResponse {
            objects: page.objects.into_iter().map(object_meta).collect(),
            is_truncated: page.is_truncated,
            next_continuation_token: page.next_continuation_token,
        }))
    }

    async fn create_multipart_upload(
        &self,
        request: Request<api::CreateMultipartUploadRequest>,
    ) -> std::result::Result<Response<api::CreateMultipartUploadResponse>, Status> {
        let request = request.into_inner();
        let upload_id = self
            .object
            .create_multipart_upload(&request.bucket, &request.key)
            .map_err(status)?;
        Ok(Response::new(api::CreateMultipartUploadResponse {
            upload_id,
        }))
    }

    async fn upload_part(
        &self,
        request: Request<api::UploadPartRequest>,
    ) -> std::result::Result<Response<api::UploadPartResponse>, Status> {
        let request = request.into_inner();
        let etag = self
            .object
            .upload_part(&request.upload_id, request.part_no, &request.data)
            .map_err(status)?;
        Ok(Response::new(api::UploadPartResponse { etag }))
    }

    async fn complete_multipart_upload(
        &self,
        request: Request<api::CompleteMultipartUploadRequest>,
    ) -> std::result::Result<Response<api::ObjectMeta>, Status> {
        let meta = self
            .object
            .complete_multipart_upload(&request.into_inner().upload_id)
            .map_err(status)?;
        Ok(Response::new(object_meta(meta)))
    }

    async fn abort_multipart_upload(
        &self,
        request: Request<api::AbortMultipartUploadRequest>,
    ) -> std::result::Result<Response<api::Ack>, Status> {
        self.object
            .abort_multipart_upload(&request.into_inner().upload_id)
            .map_err(status)?;
        Ok(Response::new(api::Ack {
            ok: true,
            ..Default::default()
        }))
    }

    async fn list_parts(
        &self,
        request: Request<api::ListPartsRequest>,
    ) -> std::result::Result<Response<api::ListPartsResponse>, Status> {
        let parts = self
            .object
            .list_parts(&request.into_inner().upload_id)
            .map_err(status)?
            .into_iter()
            .map(|(part_no, size, etag)| api::PartInfo {
                part_no,
                size,
                etag,
            })
            .collect();
        Ok(Response::new(api::ListPartsResponse { parts }))
    }
}

#[tonic::async_trait]
impl SegmentControlService for P2Grpc {
    async fn list_segments(
        &self,
        request: Request<api::ListSegmentsRequest>,
    ) -> std::result::Result<Response<api::ListSegmentsResponse>, Status> {
        let mut segment_ids = Vec::new();
        let mut segments = Vec::new();
        for (engine, control) in self
            .controls(&request.into_inner().engine)
            .map_err(status)?
        {
            for segment_id in control.list_segments().map_err(status)? {
                let migration = control.migration_status(&segment_id).map_err(status)?;
                segment_ids.push(segment_id.0);
                segments.push(segment_info(&engine, migration));
            }
        }
        Ok(Response::new(api::ListSegmentsResponse {
            segment_ids,
            segments,
        }))
    }

    async fn freeze(
        &self,
        request: Request<api::SegmentRef>,
    ) -> std::result::Result<Response<api::Ack>, Status> {
        let request = request.into_inner();
        let segment = SegmentId::new(request.segment_id);
        let (engine, control) = self
            .control_for_segment(&request.engine, &segment)
            .map_err(status)?;
        let ack = if request.migration_id.trim().is_empty() {
            control.freeze(&segment).map_err(status)?;
            MigrationAck {
                status: control.migration_status(&segment).map_err(status)?,
                idempotent: false,
            }
        } else {
            control
                .prepare_migration(
                    &segment,
                    &MigrationTaskId::new(request.migration_id),
                    request.expected_route_epoch,
                )
                .map_err(status)?
        };
        Ok(Response::new(control_ack(&engine, ack)))
    }

    async fn unfreeze(
        &self,
        request: Request<api::SegmentRef>,
    ) -> std::result::Result<Response<api::Ack>, Status> {
        let request = request.into_inner();
        let segment = SegmentId::new(request.segment_id);
        let (engine, control) = self
            .control_for_segment(&request.engine, &segment)
            .map_err(status)?;
        let ack = if request.migration_id.trim().is_empty() {
            control.unfreeze(&segment).map_err(status)?;
            MigrationAck {
                status: control.migration_status(&segment).map_err(status)?,
                idempotent: false,
            }
        } else {
            control
                .cancel_migration(
                    &segment,
                    &MigrationTaskId::new(request.migration_id),
                    request.expected_route_epoch,
                )
                .map_err(status)?
        };
        Ok(Response::new(control_ack(&engine, ack)))
    }

    async fn on_migrate_complete(
        &self,
        request: Request<api::OnMigrateCompleteRequest>,
    ) -> std::result::Result<Response<api::Ack>, Status> {
        let request = request.into_inner();
        let segment = SegmentId::new(request.segment_id);
        let block_ids = request
            .new_block_ids
            .into_iter()
            .map(BlockId)
            .collect::<Vec<_>>();
        let (engine, control) = self
            .control_for_segment(&request.engine, &segment)
            .map_err(status)?;
        let ack = if request.migration_id.trim().is_empty() {
            control
                .on_migrate_complete(&segment, block_ids)
                .map_err(status)?;
            MigrationAck {
                status: control.migration_status(&segment).map_err(status)?,
                idempotent: false,
            }
        } else {
            control
                .complete_migration(
                    &segment,
                    &MigrationTaskId::new(request.migration_id),
                    request.expected_route_epoch,
                    block_ids,
                )
                .map_err(status)?
        };
        Ok(Response::new(control_ack(&engine, ack)))
    }

    async fn on_migrate_failed(
        &self,
        request: Request<api::OnMigrateFailedRequest>,
    ) -> std::result::Result<Response<api::Ack>, Status> {
        let request = request.into_inner();
        let segment = SegmentId::new(request.segment_id);
        let (engine, control) = self
            .control_for_segment(&request.engine, &segment)
            .map_err(status)?;
        let ack = if request.migration_id.trim().is_empty() {
            control
                .on_migrate_failed(&segment, &request.reason)
                .map_err(status)?;
            MigrationAck {
                status: control.migration_status(&segment).map_err(status)?,
                idempotent: false,
            }
        } else {
            control
                .fail_migration(
                    &segment,
                    &MigrationTaskId::new(request.migration_id),
                    request.expected_route_epoch,
                    &request.reason,
                )
                .map_err(status)?
        };
        Ok(Response::new(control_ack(&engine, ack)))
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    let endpoint = env::var("AETHER_P2_LISTEN").unwrap_or_else(|_| "0.0.0.0:50052".into());
    let address: SocketAddr = endpoint.parse().map_err(|error| {
        AetherError::InvalidArgument(format!("invalid listen address: {error}"))
    })?;
    let data_dir = env::var("AETHER_P2_DATA_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("/tmp/aether-engine"));
    let service = P2Grpc::new(data_dir)?;

    println!("AetherEngine P2 gRPC listening on {address}");
    tonic::transport::Server::builder()
        .add_service(VectorServiceServer::new(service.clone()))
        .add_service(ObjectServiceServer::new(service.clone()))
        .add_service(SegmentControlServiceServer::new(service))
        .serve(address)
        .await
        .map_err(|error| AetherError::Internal(error.to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn server_recovers_control_state_from_file_wal() {
        let data_dir = tempfile::tempdir().unwrap();
        let segment = SegmentId::new("restart-bucket");
        let migration = MigrationTaskId::new("server-restart");

        {
            let service = P2Grpc::new(data_dir.path().to_path_buf()).unwrap();
            service.object.create_bucket(&segment.0).unwrap();
            let (_, control) = service
                .control_for_segment("object/default", &segment)
                .unwrap();
            control
                .prepare_migration(&segment, &migration, Some(0))
                .unwrap();
        }

        let recovered = P2Grpc::new(data_dir.path().to_path_buf()).unwrap();
        assert_eq!(recovered.controls("").unwrap().len(), 3);
        let (_, control) = recovered
            .control_for_segment("object/default", &segment)
            .unwrap();
        let status = control.migration_status(&segment).unwrap();
        assert_eq!(status.state, "frozen");
        assert_eq!(
            status.migration.active_migration_id.as_ref(),
            Some(&migration)
        );
    }
}
