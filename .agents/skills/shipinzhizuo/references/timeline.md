# 时间轴和渲染

## 编辑表

每镜记录镜号、时间、对应台词或节拍、素材链接与相对文件路径、叙事动作、机位、首尾帧、接法和待处理事项。事实表和素材出处可另存。预排时间有待对齐时，标注待对齐。

## 可执行时间轴

在项目目录保存 `timeline.json`。媒体路径相对时间轴，允许绝对路径。毫秒为整数，镜头以半开区间连续铺满画面。下面的示例需要用本期素材路径替换。

```json
{
  "width": 1080,
  "height": 1920,
  "fps": 30,
  "shots": [
    {"id": "01", "kind": "video", "file": "media/archive.mp4", "start_ms": 0, "end_ms": 3000, "source_ms": 1200},
    {"id": "02", "kind": "image", "file": "media/reconstruction.png", "start_ms": 3000, "end_ms": 6000, "motion": "push"}
  ],
  "audio": [
    {"file": "audio/voice.wav", "gain": 1.0},
    {"file": "audio/music.wav", "gain": 0.18}
  ]
}
```

- `kind` 支持 `video` 和 `image`。视频可用 `source_ms` 截取，图片可用 `motion: "push"` 做居中慢推，默认静止。镜头按画幅居中裁切，重点离边缘太近时先处理素材。
- `audio` 可省略，或提供已对齐至零秒的声音轨。增益为 0 至 2 的线性倍数。内置渲染会舍弃视频自带声音，把音轨补齐或截到画面时长。需要素材原声、复杂台词间距、转场、叠字或关键帧时，先通过编辑器或代码生成对应媒体，再用时间轴输出，或者直接用合适的制作环境导出。
- 默认 1080×1920、30 fps。输出时按同一帧边界量化全部镜头，避免逐镜舍入漂移。镜头按填写顺序处理。已有输出文件会保留，换新文件名重做。

从仓库根目录运行。检查需要 Python 3.10+，渲染额外需要含 libx264 的 FFmpeg 和 FFprobe。

```sh
python3 .agents/skills/shipinzhizuo/scripts/render.py path/to/timeline.json
python3 .agents/skills/shipinzhizuo/scripts/render.py path/to/timeline.json --output path/to/review.mp4
```

脚本检查时间轴、输出帧数与基本音视频结构。出片后查看真实画面、听音轨并核对镜头语义。给剪映导出器交接时，同时带上同版媒体、时间轴和字幕，实际草稿能打开时再确认可编辑。
