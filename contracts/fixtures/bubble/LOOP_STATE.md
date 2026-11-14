# Bubble refine loop

Photos are judged from `compare/`. Fit SSIM checks that a projected photo reappears from its own camera. It does not prove the pose is located on Mars. `human_verdict` is that check.

## Tick 1 | operator set_opacity 1
### Gaps vs ref
- fit SSIM 0.0301; holdout SSIM 0.0259
- compare PNGs are the visual judge; these numbers are the revert gate
### Edit made
- set_opacity 1
### Render result
- same on fit_ssim (0.0301 -> 0.0301); holdout 0.0259 -> 0.0259
### Next
- continue
## Tick 2 | operator enable_frame fit_0
### Gaps vs ref
- fit SSIM 0.0301; holdout SSIM 0.0259
- compare PNGs are the visual judge; these numbers are the revert gate
### Edit made
- enable_frame fit_0
### Render result
- better on fit_ssim (0.0301 -> 0.8707); holdout 0.0259 -> 0.7153
### Next
- continue
## Tick 3 | operator set_fade_end_m 20
### Gaps vs ref
- fit SSIM 0.8707; holdout SSIM 0.7153
- compare PNGs are the visual judge; these numbers are the revert gate
### Edit made
- set_fade_end_m 20
### Render result
- same on fit_ssim (0.8707 -> 0.8775); holdout 0.7153 -> 0.7203
### Next
- continue
