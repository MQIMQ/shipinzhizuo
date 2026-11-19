# 视频制作

为「李奥寰宇寻奇」制作的历史叙事与纪实短视频 Codex 技能。可以从选题开始做整期，也可以接手已定稿、已录音或已有分镜的项目。

在此仓库中打开 Codex，输入其中一句即可调用。

- `$shipinzhizuo 按寻奇纪录片流程制作这期，优先用真实视频素材，交付成片。`
- `$shipinzhizuo 写一条晚清人物故事，给史料表、三个开头和三分钟口播稿。`
- `$shipinzhizuo 按图片顺序给我每段的首尾帧提示词。`
- `$shipinzhizuo 用这份定稿和配音做历史厚涂漫画分镜，只交时间和提示词。`

[技能入口](.agents/skills/shipinzhizuo/SKILL.md) · [栏目规则](.agents/skills/shipinzhizuo/references/editorial.md) · [时间轴和渲染](.agents/skills/shipinzhizuo/references/timeline.md)

内置 Python 脚本可检查普通切镜时间轴，并用 FFmpeg 合成供审片的 MP4。剪映可编辑草稿需要运行环境提供对应的剪映导出能力。平台素材、配音和生图由项目现有文件或当前可用工具提供。

工作流构思参考 [konlu/story-studio](https://github.com/konlu/story-studio)。本仓库的技能说明和脚本为独立编写。
