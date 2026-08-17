use crc32fast::Hasher;
use serde::{Deserialize, Serialize};
use std::fmt;
use thiserror::Error;
use uuid::Uuid;

pub type Result<T> = std::result::Result<T, AetherError>;

/// Stable P2 error codes defined in the design doc appendix B.
///
/// These numeric codes are part of the v0.1 interface contract exposed to
/// P3/P4 and must stay stable across releases. `AetherError` is the internal
/// Rust error type; `P2ErrorCode` is the wire-stable classification.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum P2ErrorCode {
    NotFound = 1001,
    Conflict = 1002,
    Timeout = 1003,
    IndexNotReady = 1004,
    QuotaExceeded = 1005,
    InvalidParam = 1006,
    Upstream = 1007,
    ResultTooLarge = 1008,
    SegmentFrozen = 1009,
    Internal = 1099,
}

impl P2ErrorCode {
    pub fn numeric(self) -> u16 {
        self as u16
    }

    pub fn as_str(self) -> &'static str {
        match self {
            P2ErrorCode::NotFound => "P2Err_NotFound",
            P2ErrorCode::Conflict => "P2Err_Conflict",
            P2ErrorCode::Timeout => "P2Err_Timeout",
            P2ErrorCode::IndexNotReady => "P2Err_IndexNotReady",
            P2ErrorCode::QuotaExceeded => "P2Err_QuotaExceeded",
            P2ErrorCode::InvalidParam => "P2Err_InvalidParam",
            P2ErrorCode::Upstream => "P2Err_Upstream",
            P2ErrorCode::ResultTooLarge => "P2Err_ResultTooLarge",
            P2ErrorCode::SegmentFrozen => "P2Err_SegmentFrozen",
            P2ErrorCode::Internal => "P2Err_Internal",
        }
    }
}

impl fmt::Display for P2ErrorCode {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{} ({})", self.as_str(), self.numeric())
    }
}

#[derive(Debug, Error)]
pub enum AetherError {
    #[error("not found: {0}")]
    NotFound(String),

    #[error("already exists: {0}")]
    AlreadyExists(String),

    #[error("conflict: {0}")]
    Conflict(String),

    #[error("invalid argument: {0}")]
    InvalidArgument(String),

    #[error("unsupported: {0}")]
    Unsupported(String),

    #[error("timeout: {0}")]
    Timeout(String),

    #[error("index not ready: {0}")]
    IndexNotReady(String),

    #[error("quota exceeded: {0}")]
    QuotaExceeded(String),

    #[error("result too large: {0}")]
    ResultTooLarge(String),

    #[error("segment frozen: {0}")]
    SegmentFrozen(String),

    #[error("corrupted data: {0}")]
    Corrupted(String),

    #[error("io error: {0}")]
    Io(String),

    #[error("serde error: {0}")]
    Serde(String),

    #[error("storage error: {0}")]
    Storage(String),

    #[error("upstream (P1) error: {0}")]
    Upstream(String),

    #[error("internal error: {0}")]
    Internal(String),
}

impl AetherError {
    /// Map an internal error onto the wire-stable P2 error code contract.
    pub fn code(&self) -> P2ErrorCode {
        match self {
            AetherError::NotFound(_) => P2ErrorCode::NotFound,
            AetherError::AlreadyExists(_) | AetherError::Conflict(_) => P2ErrorCode::Conflict,
            AetherError::Timeout(_) => P2ErrorCode::Timeout,
            AetherError::IndexNotReady(_) => P2ErrorCode::IndexNotReady,
            AetherError::QuotaExceeded(_) => P2ErrorCode::QuotaExceeded,
            AetherError::InvalidArgument(_) | AetherError::Unsupported(_) => {
                P2ErrorCode::InvalidParam
            }
            AetherError::ResultTooLarge(_) => P2ErrorCode::ResultTooLarge,
            AetherError::SegmentFrozen(_) => P2ErrorCode::SegmentFrozen,
            AetherError::Storage(_) | AetherError::Upstream(_) => P2ErrorCode::Upstream,
            AetherError::Corrupted(_)
            | AetherError::Io(_)
            | AetherError::Serde(_)
            | AetherError::Internal(_) => P2ErrorCode::Internal,
        }
    }
}

impl From<std::io::Error> for AetherError {
    fn from(value: std::io::Error) -> Self {
        Self::Io(value.to_string())
    }
}

impl From<serde_json::Error> for AetherError {
    fn from(value: serde_json::Error) -> Self {
        Self::Serde(value.to_string())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum EngineKind {
    Vector = 1,
    Object = 2,
    Graph = 3,
    File = 4,
    Block = 5,
    TimeSeries = 6,
}

impl EngineKind {
    pub fn stable_code(self) -> u16 {
        self as u16
    }

    pub fn as_str(self) -> &'static str {
        match self {
            EngineKind::Vector => "vector",
            EngineKind::Object => "object",
            EngineKind::Graph => "graph",
            EngineKind::File => "file",
            EngineKind::Block => "block",
            EngineKind::TimeSeries => "timeseries",
        }
    }
}

impl fmt::Display for EngineKind {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct EngineInstanceId {
    pub kind: EngineKind,
    pub instance: String,
}

impl EngineInstanceId {
    pub fn new(kind: EngineKind, instance: impl Into<String>) -> Self {
        Self {
            kind,
            instance: instance.into(),
        }
    }
}

impl fmt::Display for EngineInstanceId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}:{}", self.kind, self.instance)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct NamespaceId(pub String);

impl NamespaceId {
    pub fn new(value: impl Into<String>) -> Self {
        Self(value.into())
    }

    pub fn default_for_engine(kind: EngineKind) -> Self {
        Self(format!("default-{}", kind.as_str()))
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct SegmentId(pub String);

impl SegmentId {
    pub fn new(value: impl Into<String>) -> Self {
        Self(value.into())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct BlockId(pub String);

impl BlockId {
    pub fn random() -> Self {
        Self(Uuid::new_v4().to_string())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct SnapshotId(pub String);

impl SnapshotId {
    pub fn random() -> Self {
        Self(Uuid::new_v4().to_string())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct MigrationTaskId(pub String);

impl MigrationTaskId {
    pub fn new(value: impl Into<String>) -> Self {
        Self(value.into())
    }

    pub fn random() -> Self {
        Self(Uuid::new_v4().to_string())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum MigrationOutcome {
    Completed,
    Failed,
    Cancelled,
}

impl MigrationOutcome {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Completed => "completed",
            Self::Failed => "failed",
            Self::Cancelled => "cancelled",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct MigrationState {
    pub route_epoch: u64,
    pub block_ids: Vec<BlockId>,
    pub active_migration_id: Option<MigrationTaskId>,
    pub active_route_epoch: Option<u64>,
    pub last_migration_id: Option<MigrationTaskId>,
    pub last_route_epoch: Option<u64>,
    pub last_outcome: Option<MigrationOutcome>,
}

impl MigrationState {
    pub fn prepare(
        &mut self,
        migration_id: &MigrationTaskId,
        expected_route_epoch: Option<u64>,
    ) -> Result<bool> {
        if let Some(active) = &self.active_migration_id {
            if active != migration_id {
                return Err(AetherError::Conflict(format!(
                    "migration {} is already active",
                    active.0
                )));
            }
            Self::check_epoch(
                expected_route_epoch,
                self.active_route_epoch.unwrap_or(self.route_epoch),
            )?;
            return Ok(true);
        }

        if self.last_migration_id.as_ref() == Some(migration_id) {
            Self::check_epoch(
                expected_route_epoch,
                self.last_route_epoch.unwrap_or(self.route_epoch),
            )?;
            return Ok(true);
        }

        Self::check_epoch(expected_route_epoch, self.route_epoch)?;
        self.active_migration_id = Some(migration_id.clone());
        self.active_route_epoch = Some(self.route_epoch);
        Ok(false)
    }

    pub fn complete(
        &mut self,
        migration_id: &MigrationTaskId,
        expected_route_epoch: Option<u64>,
        new_block_ids: Vec<BlockId>,
    ) -> Result<bool> {
        if self.active_migration_id.as_ref() == Some(migration_id) {
            let base_epoch = self.active_route_epoch.unwrap_or(self.route_epoch);
            Self::check_epoch(expected_route_epoch, base_epoch)?;
            self.route_epoch = self
                .route_epoch
                .checked_add(1)
                .ok_or_else(|| AetherError::Conflict("route epoch overflow".into()))?;
            self.block_ids = new_block_ids;
            self.finish(migration_id, base_epoch, MigrationOutcome::Completed);
            return Ok(false);
        }

        if self.active_migration_id.is_some() {
            return Err(self.active_conflict(migration_id));
        }
        if self.last_migration_id.as_ref() == Some(migration_id)
            && self.last_outcome == Some(MigrationOutcome::Completed)
        {
            Self::check_epoch(
                expected_route_epoch,
                self.last_route_epoch.unwrap_or(self.route_epoch),
            )?;
            if self.block_ids != new_block_ids {
                return Err(AetherError::Conflict(format!(
                    "migration {} was completed with a different block route",
                    migration_id.0
                )));
            }
            return Ok(true);
        }
        Err(AetherError::Conflict(format!(
            "migration {} is not active",
            migration_id.0
        )))
    }

    pub fn fail(
        &mut self,
        migration_id: &MigrationTaskId,
        expected_route_epoch: Option<u64>,
    ) -> Result<bool> {
        self.finish_without_route_change(
            migration_id,
            expected_route_epoch,
            MigrationOutcome::Failed,
        )
    }

    pub fn cancel(
        &mut self,
        migration_id: &MigrationTaskId,
        expected_route_epoch: Option<u64>,
    ) -> Result<bool> {
        self.finish_without_route_change(
            migration_id,
            expected_route_epoch,
            MigrationOutcome::Cancelled,
        )
    }

    fn finish_without_route_change(
        &mut self,
        migration_id: &MigrationTaskId,
        expected_route_epoch: Option<u64>,
        outcome: MigrationOutcome,
    ) -> Result<bool> {
        if self.active_migration_id.as_ref() == Some(migration_id) {
            let base_epoch = self.active_route_epoch.unwrap_or(self.route_epoch);
            Self::check_epoch(expected_route_epoch, base_epoch)?;
            self.finish(migration_id, base_epoch, outcome);
            return Ok(false);
        }

        if self.active_migration_id.is_some() {
            return Err(self.active_conflict(migration_id));
        }
        if self.last_migration_id.as_ref() == Some(migration_id)
            && self.last_outcome == Some(outcome)
        {
            Self::check_epoch(
                expected_route_epoch,
                self.last_route_epoch.unwrap_or(self.route_epoch),
            )?;
            return Ok(true);
        }
        Err(AetherError::Conflict(format!(
            "migration {} is not active",
            migration_id.0
        )))
    }

    fn finish(
        &mut self,
        migration_id: &MigrationTaskId,
        base_epoch: u64,
        outcome: MigrationOutcome,
    ) {
        self.active_migration_id = None;
        self.active_route_epoch = None;
        self.last_migration_id = Some(migration_id.clone());
        self.last_route_epoch = Some(base_epoch);
        self.last_outcome = Some(outcome);
    }

    fn check_epoch(expected: Option<u64>, actual: u64) -> Result<()> {
        if let Some(expected) = expected {
            if expected != actual {
                return Err(AetherError::Conflict(format!(
                    "stale route epoch {expected}; current migration route epoch is {actual}"
                )));
            }
        }
        Ok(())
    }

    fn active_conflict(&self, migration_id: &MigrationTaskId) -> AetherError {
        AetherError::Conflict(format!(
            "migration {} cannot complete while migration {} is active",
            migration_id.0,
            self.active_migration_id
                .as_ref()
                .map(|id| id.0.as_str())
                .unwrap_or("unknown")
        ))
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
pub struct Lsn(pub u64);

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum OperationKind {
    CreateNamespace = 1,
    Insert = 2,
    Delete = 3,
    PutObject = 4,
    DeleteObject = 5,
    CreateNode = 6,
    CreateEdge = 7,
    Snapshot = 8,
    Commit = 9,
    Prepare = 10,
    ProjectVector = 11,
    MigrationPrepare = 12,
    MigrationComplete = 13,
    MigrationFailed = 14,
    MigrationCancelled = 15,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum StorageTier {
    L0Dram,
    L1Nvme,
    L2Hdd,
    L3Object,
    L4Archive,
}

/// Unified Segment lifecycle state machine (design doc §2.3.1).
///
/// Growing  -> accepting writes, brute-force scan, no index.
/// Sealing  -> reached size cap, building index, read-only.
/// Sealed   -> index built, queries go through index, migratable by P3.
/// Frozen   -> P3 froze it before migration, read-only.
/// Archived -> data moved to L3/L4, recalled on demand by P3.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum SegmentState {
    Growing,
    Sealing,
    Sealed,
    Frozen,
    Archived,
}

impl SegmentState {
    pub fn as_str(self) -> &'static str {
        match self {
            SegmentState::Growing => "growing",
            SegmentState::Sealing => "sealing",
            SegmentState::Sealed => "sealed",
            SegmentState::Frozen => "frozen",
            SegmentState::Archived => "archived",
        }
    }

    /// Whether new writes may land in this segment.
    pub fn accepts_writes(self) -> bool {
        matches!(self, SegmentState::Growing)
    }

    /// Whether this segment participates in query fan-out by default.
    pub fn is_queryable(self) -> bool {
        !matches!(self, SegmentState::Archived)
    }
}

impl fmt::Display for SegmentState {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SegmentDescriptor {
    pub segment_id: SegmentId,
    pub namespace_id: NamespaceId,
    pub engine: EngineKind,
    pub tier: StorageTier,
    pub hot_score: f64,
    pub sealed: bool,
    pub bytes: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CompactPolicy {
    pub max_segment_bytes: u64,
    pub force: bool,
}

impl Default for CompactPolicy {
    fn default() -> Self {
        Self {
            max_segment_bytes: 128 * 1024 * 1024,
            force: false,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RecoveryReport {
    pub replayed_records: usize,
    pub skipped_records: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CompactReport {
    pub compacted_segments: usize,
    pub reclaimed_bytes: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct Checksum(pub u32);

pub fn crc32(data: &[u8]) -> Checksum {
    let mut hasher = Hasher::new();
    hasher.update(data);
    Checksum(hasher.finalize())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn error_codes_match_contract() {
        assert_eq!(AetherError::NotFound("x".into()).code().numeric(), 1001);
        assert_eq!(AetherError::Conflict("x".into()).code().numeric(), 1002);
        assert_eq!(
            AetherError::AlreadyExists("x".into()).code().numeric(),
            1002
        );
        assert_eq!(AetherError::Timeout("x".into()).code().numeric(), 1003);
        assert_eq!(
            AetherError::IndexNotReady("x".into()).code().numeric(),
            1004
        );
        assert_eq!(
            AetherError::QuotaExceeded("x".into()).code().numeric(),
            1005
        );
        assert_eq!(
            AetherError::InvalidArgument("x".into()).code().numeric(),
            1006
        );
        assert_eq!(AetherError::Upstream("x".into()).code().numeric(), 1007);
        assert_eq!(
            AetherError::ResultTooLarge("x".into()).code().numeric(),
            1008
        );
        assert_eq!(
            AetherError::SegmentFrozen("x".into()).code().numeric(),
            1009
        );
        assert_eq!(
            AetherError::NotFound("x".into()).code().as_str(),
            "P2Err_NotFound"
        );
    }

    #[test]
    fn migration_state_is_idempotent_and_rejects_stale_callbacks() {
        let mut state = MigrationState::default();
        let migration = MigrationTaskId::new("migration-1");

        assert!(!state.prepare(&migration, Some(0)).unwrap());
        assert!(state.prepare(&migration, Some(0)).unwrap());
        assert!(state
            .prepare(&MigrationTaskId::new("migration-2"), Some(0))
            .is_err());
        assert!(state
            .complete(&migration, Some(1), vec![BlockId("block-1".into())])
            .is_err());

        assert!(!state
            .complete(&migration, Some(0), vec![BlockId("block-1".into())])
            .unwrap());
        assert_eq!(state.route_epoch, 1);
        assert!(state
            .complete(&migration, Some(0), vec![BlockId("block-1".into())])
            .unwrap());
        assert_eq!(state.route_epoch, 1);

        let next = MigrationTaskId::new("migration-2");
        assert!(state.prepare(&next, Some(0)).is_err());
        assert!(!state.prepare(&next, Some(1)).unwrap());
        assert!(state.fail(&next, Some(0)).is_err());
        assert!(!state.fail(&next, Some(1)).unwrap());
        assert_eq!(state.route_epoch, 1);
        assert_eq!(state.last_outcome, Some(MigrationOutcome::Failed));
    }
}
