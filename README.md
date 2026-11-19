# 视频故事工坊

做历史人物传记、历史叙事、书籍改编类图文视频的完整流程。主线是历史类，书籍改编（与神对话线）是第二条线。

先做一条，再回头改流程。

## 这个 skill 是什么

- 一份完整工作流（`SKILL.md`）：从选题、溯源脚本、确认、配音、配图、分镜、合成、质检到交付，共 10 步。主线是历史类，书籍改编线是第二条线。
- 一个案例库（`seed-library/`）：与神对话 7 条种子 + 历史 6 条种子 + 历史选题清单。配套写作 DNA 覆盖开头、结构和口播用语。
- 两个脚本（`scripts/`）：豆包配音 + FFmpeg 合成出片，都是能直接跑的。
- 一份计划示例（`plan.json`）：照着填，就能出片。

成品：旁白 + 分段配图 + 轻动效的长视频（主力 6 分钟左右），历史线默认 9:16 竖版，书籍问答线 3:4（1080x1440），mp4 直接出片，封面剪进片头前 0.5 秒。

## 安装后直接用

解压安装包，把下面这句话发给有文件操作权限的 Agent。

> 请把我提供的 `video-story-workshop` 文件夹安装到你的 Skills 目录，保留完整目录结构。安装后用 `video-story-workshop` 帮我做一条竖版历史短片。

Codex CLI 安装时，把解压后的 `video-story-workshop` 目录整个复制到 `~/.agents/skills/`。

装好后，按你手上的素材直接交代任务。

- `用 $video-story-workshop 做一条 45 秒的历史故事短片，从查资料到导出竖版 MP4，直接做。`
- `用 $video-story-workshop，稿子和旁白都有了，按这段音频给我分镜。`
- `用 $video-story-workshop，用这个文件夹里的旁白和图片直接出 MP4。`

| 要做的事 | Agent 按需检查 |
|---|---|
| 选题、写稿、提示词 | 使用当前 Agent 的写作能力。核查史料时使用可用的检索工具或用户提供的资料。 |
| 实际配图 | 优先复用现有图片，其次用当前 Agent 的生图工具。缺少图片和生图能力时先交分镜和提示词。 |
| 配音 | 优先复用现成音频。豆包需要 Python 3.10+、FFmpeg、`websockets` 和语音服务 Key；也可用用户指定的 Agent 自带配音工具。 |
| 合成 MP4 | Python 3.10+、FFmpeg（含 `ffprobe`、libx264 和 xfade）。Agent 按需要检查和配置。 |

## 怎么跑：一条片子四步

### 1. 写脚本（按 SKILL.md 第 0–3 步）

选题、溯源、确认。最重要的一条：书籍线每一规划段落先在原著里找到出处再动笔，原书真说过的话才能标成原话，自己补的衔接一律是旁白口吻；历史线每条断言先在史料里找到出处再动笔，建史料出处表。脚本 + 出处表 + 事实核查表一起确认，没确认不开工。

### 2. 配音（第 4 步）

按段合成。脚本调用豆包 [V3 双向 WebSocket](https://docs.volcengine.com/docs/doubao-voice/bidirectional-streaming-text-to-speech-websocket?lang=zh)，资源固定为普通音色 `seed-tts-2.0`。

```bash
python3 -m pip install "websockets>=15,<17"
python3 scripts/doubao_tts.py --text-file seg1.txt --output seg1.mp3 --dry-run
python3 scripts/doubao_tts.py --text-file seg1.txt --output seg1.mp3 --key-stdin
```

- 默认音色是云舟。想换音色：`--speaker zh_male_liufei_uranus_bigtts`（刘飞 2.0）、`zh_female_xiaohe_uranus_bigtts`（小何 2.0）、`zh_female_vv_uranus_bigtts`（Vivi）。只有这四个预置音色，不克隆声音。
- 在终端使用 `--key-stdin` 时会隐藏输入。自动化可从宿主的密钥配置读取 `DOUBAO_TTS_API_KEY` 并省略该参数。
- `--dry-run` 只检查本地输入。真实合成收到会话结束事件后，脚本会验证 MP3 可完整解码再保存。
- 每段合成完立刻用 ffprobe 量出真实秒数并记下来，再按顺序拼成完整旁白。

### 3. 配图和分镜（第 6–7 步）

长段落可用 4 张图按起承转合递进，短片按语义和目标时长分镜。先出分镜表和 1 张测试图，确认长相和风格后再批量生成。人物描述全片同一份；图里不带任何文字；单人旁白不出现正对镜头张嘴说话的脸。

### 4. 合成出片（第 8–10 步）

按分段时长写一份 `plan.json`（照着本 skill 根目录的示例填），然后：

```bash
python3 scripts/render.py --plan plan.json --output final.mp4
python3 scripts/render.py --plan plan.json --check-only   # 只核验计划，不渲染
```

- 输出就是 mp4（h264 + aac），不用进剪映。
- 封面填进 plan.json 的 `cover` 字段，会占片头前 0.5 秒，旁白照常播放，总时长不变。第一镜的时长会自动减 0.5 秒，所以第一镜至少要 0.5 秒以上。
- 每张图缓慢推近，图与图之间 0.8 秒交叉淡化，不烧录字幕。
- `--check-only` 会检查素材和旁白覆盖。渲染完成后核对帧数、画幅、音轨和完整解码，通过后才保存最终文件。

## 文件说明

| 文件 | 说明 |
|---|---|
| `SKILL.md` | 完整工作流：10 步 + 铁律 + 检查表 + 带货口播格式 + 开头速查（历史主线） |
| `README.md` | 本文件：安装使用说明 |
| `seed-library/` | 案例库：与神对话 7 条 + 历史 6 条 + 历史选题清单 |
| `LICENSE` | MIT 许可证。分发时请附上本文件 |
| `scripts/doubao_tts.py` | 豆包 TTS 配音：四个预置音色，默认云舟，不克隆 |
| `scripts/render.py` | FFmpeg 合成：图片 + 旁白 → mp4，封面 0.5 秒开场，画幅按 plan.json |
| `plan.json` | 合成计划示例 |

## 常见问题

**配音报鉴权或额度错误**：停在配音这一步，先去控制台核对 Key 和服务开通状态，不要反复重试（失败也可能计费）。

**计划时长与旁白不符**：先用 ffprobe 实测最终音频，再按分段时长调整镜头。运行 `--check-only` 通过后出片。

**切点处人物突然换姿势**：用前一镜的末帧做参考，重新生成后一镜，再复查这个切点，不要整段推倒。

**图里出现乱码文字**：不要让 AI 在图里写字。图内零文字，标题走封面排版。

**文案被指“书里根本没这话”**：回第 2 步，逐段补出处或改成旁白口吻。没有出处的段落，要么删掉，要么改成明确的旁白讨论，永远不要标成原书原话。

## 三条铁律（再提醒一次）

1. 脚本没确认，不配音、不配图。
2. 音频定了就不改，后面迁就音频。
3. 出错只重做出错的那一段，不整批推倒。
