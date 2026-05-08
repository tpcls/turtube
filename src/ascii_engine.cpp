#include <cstdint>
#include <cmath>
#include <cstring>
#include <algorithm>
#include <chrono>
#include <cstdio>

#ifdef _OPENMP
#include <omp.h>
#endif

extern "C" {

void resize_frame_fast(
    const uint8_t* __restrict__ src_data,
    int src_h, int src_w,
    uint8_t* __restrict__ dst_data,
    int dst_h, int dst_w
) {
    const int h_ratio = ((src_h - 1) << 16) / (dst_h > 1 ? dst_h - 1 : 1);
    const int w_ratio = ((src_w - 1) << 16) / (dst_w > 1 ? dst_w - 1 : 1);

    #ifdef _OPENMP
    #pragma omp parallel for schedule(static)
    #endif
    for (int y = 0; y < dst_h; y++) {
        const int src_y_fp = y * h_ratio;
        const int y0 = src_y_fp >> 16;
        const int y1 = (y0 + 1 < src_h) ? y0 + 1 : y0;
        const int dy = src_y_fp & 0xFFFF;

        const uint8_t* row0 = src_data + y0 * src_w * 3;
        const uint8_t* row1 = src_data + y1 * src_w * 3;
        uint8_t* dst_row = dst_data + y * dst_w * 3;

        for (int x = 0; x < dst_w; x++) {
            const int src_x_fp = x * w_ratio;
            const int x0 = src_x_fp >> 16;
            const int x1 = (x0 + 1 < src_w) ? x0 + 1 : x0;
            const int dx = src_x_fp & 0xFFFF;

            const int w00 = ((65536 - dx) * (65536 - dy)) >> 16;
            const int w10 = (dx            * (65536 - dy)) >> 16;
            const int w01 = ((65536 - dx) * dy)            >> 16;
            const int w11 = (dx            * dy)            >> 16;

            const uint8_t* p00 = row0 + x0 * 3;
            const uint8_t* p10 = row0 + x1 * 3;
            const uint8_t* p01 = row1 + x0 * 3;
            const uint8_t* p11 = row1 + x1 * 3;

            dst_row[x * 3 + 0] = (uint8_t)((w00*p00[0] + w10*p10[0] + w01*p01[0] + w11*p11[0]) >> 16);
            dst_row[x * 3 + 1] = (uint8_t)((w00*p00[1] + w10*p10[1] + w01*p01[1] + w11*p11[1]) >> 16);
            dst_row[x * 3 + 2] = (uint8_t)((w00*p00[2] + w10*p10[2] + w01*p01[2] + w11*p11[2]) >> 16);
        }
    }
}

void rgb_to_gray_index(
    const uint8_t* __restrict__ rgb_data,
    int h, int w,
    uint8_t* __restrict__ index_out,
    int palette_len
) {
    // Rec.709 Q15 정수 가중치 (BGR 순서)
    // [버그수정] 원본: B_WEIGHT*idx+0, G*idx+1, R*idx+2 → 가중치가 BGR과 반대로 적용됨
    const int R_W = 6966;   // 0.2126 * 32768
    const int G_W = 23431;  // 0.7152 * 32768
    const int B_W = 2366;   // 0.0722 * 32768

    #ifdef _OPENMP
    #pragma omp parallel for schedule(static)
    #endif
    for (int y = 0; y < h; y++) {
        const uint8_t* row = rgb_data + y * w * 3;
        uint8_t* out_row = index_out + y * w;
        for (int x = 0; x < w; x++) {
            const int b = row[x * 3 + 0];
            const int g = row[x * 3 + 1];
            const int r = row[x * 3 + 2];
            const int gray = (R_W * r + G_W * g + B_W * b) >> 15;
            int idx = (gray * palette_len) >> 8;
            if (idx < 0) idx = 0;
            if (idx > palette_len) idx = palette_len;
            out_row[x] = (uint8_t)idx;
        }
    }
}

static inline char* write_uint8(char* p, int v) {
    if (v >= 100) { *p++ = '0' + v / 100; v %= 100; *p++ = '0' + v / 10; }
    else if (v >= 10) { *p++ = '0' + v / 10; }
    *p++ = '0' + v % 10;
    return p;
}

// [최적화] 연속된 같은 색상은 이스케이프 생략, snprintf 대신 직접 정수 변환
void build_ansi_string(
    const uint8_t* __restrict__ rgb_data,
    const uint8_t* __restrict__ idx_data,
    const char* palette,
    int palette_len,
    int h, int w,
    char* output,
    int output_size,
    int color_levels,
    int color_block_width,
    int* output_len
) {
    char* p = output;
    char* end = output + output_size - 32;

    int prev_r = -1, prev_g = -1, prev_b = -1;
    const int levels = color_levels < 2 ? 256 : color_levels;
    const int block_w = color_block_width < 1 ? 1 : color_block_width;

    for (int y = 0; y < h && p < end; y++) {
        for (int x = 0; x < w && p < end; x++) {
            const int flat = y * w + x;
            uint8_t pal_idx = idx_data[flat];
            if (pal_idx > palette_len) pal_idx = palette_len;

            const int color_x = (x / block_w) * block_w;
            const int color_flat = y * w + color_x;
            int b = rgb_data[color_flat * 3 + 0];
            int g = rgb_data[color_flat * 3 + 1];
            int r = rgb_data[color_flat * 3 + 2];

            if (levels < 256) {
                const int scale = levels - 1;
                const int b_bucket = (b * scale + 127) / 255;
                const int g_bucket = (g * scale + 127) / 255;
                const int r_bucket = (r * scale + 127) / 255;
                b = (b_bucket * 255 + scale / 2) / scale;
                g = (g_bucket * 255 + scale / 2) / scale;
                r = (r_bucket * 255 + scale / 2) / scale;
            }

            if (r != prev_r || g != prev_g || b != prev_b) {
                *p++ = '\x1b'; *p++ = '['; *p++ = '3'; *p++ = '8'; *p++ = ';';
                *p++ = '2'; *p++ = ';';
                p = write_uint8(p, r); *p++ = ';';
                p = write_uint8(p, g); *p++ = ';';
                p = write_uint8(p, b); *p++ = 'm';
                prev_r = r; prev_g = g; prev_b = b;
            }
            *p++ = palette[pal_idx];
        }
        if (p < end) {
            *p++ = '\x1b'; *p++ = '['; *p++ = '0'; *p++ = 'm'; *p++ = '\n';
        }
        prev_r = prev_g = prev_b = -1;
    }

    *output_len = (int)(p - output);
}

long long benchmark_resize(
    const uint8_t* src_data,
    int src_h, int src_w,
    uint8_t* dst_data,
    int dst_h, int dst_w,
    int iterations
) {
    auto start = std::chrono::high_resolution_clock::now();
    for (int i = 0; i < iterations; i++) {
        resize_frame_fast(src_data, src_h, src_w, dst_data, dst_h, dst_w);
    }
    auto end_t = std::chrono::high_resolution_clock::now();
    return std::chrono::duration_cast<std::chrono::microseconds>(end_t - start).count();
}

} // extern "C"
