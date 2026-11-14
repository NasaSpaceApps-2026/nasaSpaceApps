# Bubble refine loop

Photos are judged from `compare/`. Fit SSIM checks that a projected photo reappears from its own camera. It does not prove the pose is located on Mars. `human_verdict` is that check.

## Tick 1 | operator set_opacity 1
### Gaps vs ref
- fit SSIM 0.0643; holdout SSIM 0.0995
- compare PNGs are the visual judge; these numbers are the revert gate
### Edit made
- set_opacity 1
### Render result
- same on fit_ssim (0.0643 -> 0.0643); holdout 0.0995 -> 0.0995
### Next
- continue
## Tick 2 | operator enable_frame fit_0
### Gaps vs ref
- fit SSIM 0.0643; holdout SSIM 0.0995
- compare PNGs are the visual judge; these numbers are the revert gate
### Edit made
- enable_frame fit_0
### Render result
- better on fit_ssim (0.0643 -> 0.6654); holdout 0.0995 -> 0.5220
### Next
- continue
## Tick 3 | operator set_fade_end_m 20
### Gaps vs ref
- fit SSIM 0.6654; holdout SSIM 0.5220
- compare PNGs are the visual judge; these numbers are the revert gate
### Edit made
- set_fade_end_m 20
### Render result
- better on fit_ssim (0.6654 -> 0.6757); holdout 0.5220 -> 0.5304
### Next
- continue
