use std::arch::x86_64::*;
use std::env;
use std::fs;
use std::hint::black_box;
use std::process::Command;
use std::time::{Instant, SystemTime, UNIX_EPOCH};

#[cfg(windows)]
#[link(name = "kernel32")]
unsafe extern "system" {
    fn GetCurrentProcess() -> *mut core::ffi::c_void;
    fn SetProcessAffinityMask(process: *mut core::ffi::c_void, mask: usize) -> i32;
}

#[cfg(windows)]
fn pin_to_cpu_zero() -> bool {
    unsafe { SetProcessAffinityMask(GetCurrentProcess(), 1) != 0 }
}

#[cfg(not(windows))]
fn pin_to_cpu_zero() -> bool {
    false
}

fn matrix(size: usize) -> Vec<f32> {
    let mut values = vec![0.0; size * size];
    for row in 0..size {
        for column in 0..size {
            let raw = ((row as u64 * 1_315_423_911 + column as u64 * 2_654_435_761) % 1000) as f32;
            values[row * size + column] = raw / 1000.0 - 0.5;
        }
    }
    values
}

fn transpose(input: &[f32], size: usize) -> Vec<f32> {
    let mut output = vec![0.0; input.len()];
    for row in 0..size {
        for column in 0..size {
            output[column * size + row] = input[row * size + column];
        }
    }
    output
}

#[inline(never)]
fn dot_scalar(left: &[f32], right: &[f32]) -> f32 {
    let mut sum = 0.0f32;
    for index in 0..left.len() {
        sum += black_box(left[index]) * black_box(right[index]);
    }
    sum
}

#[target_feature(enable = "avx2")]
unsafe fn dot_avx2(left: &[f32], right: &[f32]) -> f32 {
    let mut sum = _mm256_setzero_ps();
    let mut index = 0;
    while index + 8 <= left.len() {
        let left_vector = unsafe { _mm256_loadu_ps(left.as_ptr().add(index)) };
        let right_vector = unsafe { _mm256_loadu_ps(right.as_ptr().add(index)) };
        sum = _mm256_add_ps(sum, _mm256_mul_ps(left_vector, right_vector));
        index += 8;
    }
    let mut lanes = [0.0f32; 8];
    unsafe { _mm256_storeu_ps(lanes.as_mut_ptr(), sum) };
    let mut result: f32 = lanes.iter().sum();
    while index < left.len() {
        result += left[index] * right[index];
        index += 1;
    }
    result
}

fn gemm_scalar(left: &[f32], right_transposed: &[f32], output: &mut [f32], size: usize) {
    for row in 0..size {
        let left_row = &left[row * size..(row + 1) * size];
        for column in 0..size {
            output[row * size + column] = dot_scalar(
                left_row,
                &right_transposed[column * size..(column + 1) * size],
            );
        }
    }
}

fn gemm_avx2(left: &[f32], right_transposed: &[f32], output: &mut [f32], size: usize) {
    for row in 0..size {
        let left_row = &left[row * size..(row + 1) * size];
        for column in 0..size {
            output[row * size + column] = unsafe {
                dot_avx2(
                    left_row,
                    &right_transposed[column * size..(column + 1) * size],
                )
            };
        }
    }
}

fn median(values: &mut [f64]) -> f64 {
    values.sort_by(f64::total_cmp);
    values[values.len() / 2]
}

fn max_abs_error(left: &[f32], right: &[f32]) -> f32 {
    left.iter()
        .zip(right)
        .map(|(left_value, right_value)| (left_value - right_value).abs())
        .fold(0.0, f32::max)
}

fn main() {
    let args: Vec<String> = env::args().collect();
    let output_index = args
        .iter()
        .position(|value| value == "--output")
        .expect("--output is required");
    let output_path = args.get(output_index + 1).expect("--output needs a path");
    let repetitions = args
        .iter()
        .position(|value| value == "--repetitions")
        .map(|index| {
            args[index + 1]
                .parse::<usize>()
                .expect("invalid repetitions")
        })
        .unwrap_or(5);
    let sizes = args
        .iter()
        .position(|value| value == "--sizes")
        .map(|index| {
            args[index + 1..]
                .iter()
                .take_while(|value| !value.starts_with("--"))
                .map(|value| value.parse::<usize>().expect("invalid size"))
                .collect::<Vec<_>>()
        })
        .unwrap_or_else(|| vec![128, 256, 512, 1024]);
    assert!(repetitions > 0 && sizes.iter().all(|size| *size > 0));

    let affinity = pin_to_cpu_zero();
    let avx2 = is_x86_feature_detected!("avx2");
    if !avx2 {
        panic!("AVX2 is required for the explicit SIMD benchmark");
    }

    let mut records = Vec::new();
    for size in sizes {
        let left = matrix(size);
        let right = matrix(size);
        let right_transposed = transpose(&right, size);
        let mut scalar = vec![0.0f32; size * size];
        let mut simd = vec![0.0f32; size * size];

        gemm_scalar(&left, &right_transposed, &mut scalar, size);
        gemm_avx2(&left, &right_transposed, &mut simd, size);
        let error = max_abs_error(&scalar, &simd);

        for implementation in ["rust_scalar", "rust_explicit_avx2"] {
            let mut times = Vec::new();
            for _ in 0..repetitions {
                let started = Instant::now();
                if implementation == "rust_scalar" {
                    gemm_scalar(&left, &right_transposed, &mut scalar, size);
                } else {
                    gemm_avx2(&left, &right_transposed, &mut simd, size);
                }
                times.push(started.elapsed().as_secs_f64());
            }
            let best = times.iter().copied().fold(f64::INFINITY, f64::min);
            let median_seconds = median(&mut times);
            let current = if implementation == "rust_scalar" {
                &scalar
            } else {
                &simd
            };
            let checksum: f64 = current.iter().map(|value| *value as f64).sum();
            records.push(format!(
                "{{\"implementation\":\"{}\",\"size\":{},\"dtype\":\"float32\",\"repetitions\":{},\"median_ms\":{:.6},\"min_ms\":{:.6},\"gflops_median\":{:.6},\"checksum\":{:.9},\"finite\":{},\"max_abs_error_vs_scalar\":{:.9}}}",
                implementation,
                size,
                repetitions,
                median_seconds * 1000.0,
                best * 1000.0,
                (2.0 * (size as f64).powi(3)) / median_seconds / 1e9,
                checksum,
                current.iter().all(|value| value.is_finite()),
                if implementation == "rust_scalar" { 0.0 } else { error },
            ));
        }
    }

    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system time is before Unix epoch")
        .as_secs();
    let rustc = Command::new("rustc")
        .arg("--version")
        .output()
        .ok()
        .map(|output| String::from_utf8_lossy(&output.stdout).trim().to_owned())
        .unwrap_or_else(|| "unknown".to_owned());
    let json = format!(
        "{{\n  \"timestamp_epoch\": {},\n  \"rustc\": \"{}\",\n  \"affinity_cpu0\": {},\n  \"avx2\": {},\n  \"fma\": {},\n  \"avx512f\": {},\n  \"results\": [\n    {}\n  ]\n}}\n",
        timestamp,
        rustc,
        affinity,
        avx2,
        is_x86_feature_detected!("fma"),
        is_x86_feature_detected!("avx512f"),
        records.join(",\n    "),
    );
    fs::write(output_path, &json).expect("failed to write output");
    println!("{json}");
}
