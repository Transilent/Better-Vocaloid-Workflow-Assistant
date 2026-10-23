# 显式语境试听工程

2026-10-20 已停止自动语义识别路线。显式适配器及回执继续用于人工选择、经审阅的局部试验；已有语义 A/B 为历史候选。新的纯加密对照不包含语义配方变化。正常 SVP 导出可选择 `expression_density=original|dense`，后者保留原线性曲线形状，并给持音期间已有参数按 5 ms 补点，不凭空创建空参数。

[English](ContextAdapter.en.md)

语境工程CLI可以生成独立可恢复的A/B工程。A保留输入工程已有表达；B通过显式配置，在经审阅的局部起音、持音或收尾上增加变化。这是待试听的实验能力；工程有效不代表声库响应已验证、语义识别正确或听感更好。

在应用根目录使用已生成的运行模块，不直接启动`src/launch.py`：

```powershell
& '.\dependencies\vocal2midi\python\python.exe' -B '.\context_workflow.py' project prepare --project 'song.svp' --lyrics 'lyrics.json' --language ja --output 'analysis'
& '.\dependencies\vocal2midi\python\python.exe' -B '.\context_workflow.py' project apply --bundle 'analysis' --application 'application.json' --profile 'profile.json' --output 'audition'
```

`lyrics.json`按`lead`、`backing`等声部键提供原始文字。缺失声部的文字保持未知。可用`--words`按声部提供显式词跨度，或`--readings`提供带出处的原文跨度和读音token。日语可离线使用已经安装的OpenJTalk字典；缺字典时明确报错，不自动下载，不加载ASR模型。不能确定单字读音归属的日语多字词，保留前端给出的完整表层词跨度，不假造逐字精度。

分析目录包含`baseline.svp`、`alignment.json`、`evidence.json`、`readings.json`、`proposal.json`、`inputs.json`及文件指纹清单。旧证据API默认仍使用原来的完整序列校验；新定位是显式启用，只保留有序token关联中没有歧义的位置，错字和漏字局部留空，重复片段存在多个落点时保留未知。这是文字/读音关联，不是经过声学验证的强制对齐。

应用配置显式保存证据ID、声部/轨道绑定、音符ID、原文跨度/引文、起持收相位、配方、幅度和理由。适配profile注明目标声库、是否允许未绑定声库，并声明各相位配方的数值增量，状态必须是`experimental_unrendered`。自由文本情绪不会自动选择数值；配方与实测声库校准响应分开，不把某首歌的旧校准跨曲复用。

只允许局部改变响度、张力、气息和有声成分。适配保留所有原始点的时间坐标，在变化区域高密度采样；加密严格沿原有分段线性函数增加点，不重新拟合点间样条。选中区域的原值会因本次显式调声而改变，完整原版保留在A。未选区域的连续曲线值、音符、音高、未知参数、长音原起伏、声库设置、retakes、混音和伴奏均保留。冲突手势、覆盖同轨未选重叠音符的窗口、过时证据、错误轨道绑定、被选中的不支持插值、越界数值会明确拒绝。当前支持未裁剪的平铺音符组；链接组/裁剪组明确报错，不做半套处理。

应用成功后生成`A-before.svp`、`B-context-trial.svp`、原样配置和回执。只有输入与所有登记文件指纹一致才复用已有输出；改过的产物不会覆盖。中断暂存目录不算完成，原任务、已登记工程、登录与原SVP不变。

请用相同声库、引擎版本、模式、混音和效果对A/B合成，结合整句比较重点片段。功能验证、声学观察、实际试听偏好分开记录。CLI不执行合成或试听；通用语义模型仍是显式可选接口。
