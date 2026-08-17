pub fn object_data_path(bucket: &str, key: &str) -> String {
    let bucket_hash = blake3::hash(bucket.as_bytes()).to_hex().to_string();
    let key_input = format!("{bucket}\0{key}");
    let key_hash = blake3::hash(key_input.as_bytes()).to_hex().to_string();
    format!(
        "objects/{}/{}/{}",
        &bucket_hash[0..16],
        &key_hash[0..2],
        key_hash
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn path_does_not_embed_user_key() {
        let path = object_data_path("bucket", "../legal/key.txt");
        assert!(!path.contains(".."));
        assert!(!path.contains("legal"));
    }
}
