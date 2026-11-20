# 视频故事工坊

制作中文图文短视频和口播稿。支持历史叙事、知识科普、教程、真实故事、书籍改编和原创故事。**没想好做什么时，推荐先用一个资料可查的历史题材试跑。** 已定题目就按自己的方向做。

## 安装后直接用

解压安装包，把下面这句话发给有文件操作权限的 Agent。

> 请把我提供的 `video-story-workshop` 文件夹安装到你的 Skills 目录，保留完整目录结构。安装后按我指定的题目写稿或做片。

Codex CLI 安装时，把解压后的 `video-story-workshop` 目录整个复制到 `~/.agents/skills/`。

- `用 $video-story-workshop 选一个有可靠史料的题，做一条 45 秒的历史短片。`
- `用 $video-story-workshop 写一个讲手机电池保养的 60 秒口播稿，附依据。`
- `用 $video-story-workshop 把我的原创故事拆成分镜，保留人物和结尾。`
- `用 $video-story-workshop，用这个文件夹里的旁白和图片直接出 MP4。`

## 从现有材料开始

| 要做的事 | Agent 按需检查 |
| --- | --- |
| 选题、口播稿、分镜提示词 | 先读 [写作 DNA](seed-library/writing-dna.md) 和 [题材写作指南](seed-library/editorial-routes.md)。事实型稿核对实际来源，原创故事核对设定。 |
| 配图 | 优先复用现有图片，也可用当前 Agent 的生图工具。长段和短段按实际内容拆镜。 |
| 配音 | 优先复用现成音频。豆包需要 Python 3.10+、FFmpeg、`websockets` 和语音服务 Key，也可用用户指定的 Agent 配音工具。 |
| 合成 MP4 | Python 3.10+、FFmpeg（含 `ffprobe`、libx264 和 xfade）。Agent 按需要配置。 |

## 配音

按段合成并保存每段，合并后实测最终旁白长度。脚本调用豆包 [V3 双向 WebSocket](https://docs.volcengine.com/docs/doubao-voice/bidirectional-streaming-text-to-speech-websocket?lang=zh)，普通音色资源为 `seed-tts-2.0`。

```bash
python3 -m pip install "websockets>=15,<17"
python3 scripts/doubao_tts.py --text-file seg1.txt --output seg1.mp3 --dry-run
python3 scripts/doubao_tts.py --text-file seg1.txt --output seg1.mp3 --key-stdin
```

默认刘飞 2.0，另可用云舟、小何 2.0 或 Vivi，运行 `--help` 查看对应音色 ID。终端用 `--key-stdin` 隐藏输入，自动化可从宿主的密钥配置读取 `DOUBAO_TTS_API_KEY`。检查命令仅核对本地输入。实际音频收到服务结束事件并通过解码校验后才保存。

## 合成

按 [计划示例](plan.json) 换成本期的旁白和图片路径，再按实际旁白长度填写镜头时长。

```bash
python3 scripts/render.py --plan plan.json --check-only
python3 scripts/render.py --plan plan.json --output final.mp4
```

- 图文短视频默认 9:16。书籍问答可沿用 3:4，用户给定画幅优先。内置合成使用图片轻推和镜间交叉淡化，输出 H.264 + AAC 的 MP4。
- `cover` 可选，占片头前 0.5 秒，总时长不变。有封面时，第一镜原时长须超过 0.5 秒。
- `--check-only` 检查计划、素材路径和时长。渲染完成后再核对帧数、画幅、音轨和完整解码，校验通过才保存成片。需要字幕时可另交字幕文件。

完整流程见 [SKILL.md](SKILL.md)。案例、写作规范和古籍入口见 [案例库](seed-library/README.md)。
