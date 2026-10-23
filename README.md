# astrbot_plugin_jmcomic

给 AstrBot 使用的 JMComic PDF 下载插件。发送 `/jm 漫画ID`，下载图片并合成为 PDF，可以直接发送文件，也可以上传到 `tmpfiles.org` 后返回临时下载链接。

## 安装

在 AstrBot 的插件页面点击右下角的 `+`，选择从链接安装，填写：

```text
https://github.com/air041001/astrbot_plugin_jmcomic
```

安装后，在插件卡片上点击齿轮进入配置。手动更新本地文件后，先点击卡片上的重载按钮，让 AstrBot 读取新增配置。

依赖由 `requirements.txt` 声明：`jmcomic`、`Pillow`、`requests`、`img2pdf`。如果自动安装依赖失败，需要在 AstrBot 实际使用的 Python 环境里安装它们。

## 配置

所有配置都可以在 AstrBot 界面中修改，不需要编辑 `main.py`。

| 配置项 | 默认值 | 作用 |
| --- | --- | --- |
| 允许在群聊中使用 | 关闭 | 关闭时只允许私聊；开启后群聊仍受使用权限限制。 |
| 使用权限 | 仅管理员 | 可选“仅管理员”或“所有人”。管理员指 AstrBot 后台设置的全局管理员。 |
| 发送方式 | 下载链接 | 可选“下载链接”或“直接发送 PDF”。 |

“仅管理员”不包含普通群主或群管理员。管理员 ID 在 AstrBot 自身的配置中设置；本插件使用 `event.is_admin()` 判断身份。选择“所有人”时，AstrBot 自身的白名单、会话配置和指令管理设置仍然生效。

下载链接模式会把 PDF 上传到第三方服务 `tmpfiles.org`，返回正常的 HTTPS 下载地址，不再替换地址里的标点。链接可能过期或上传失败。直接发送 PDF 模式不会调用这个上传接口，也不会在文件发送失败后自动改为上传网站。

## LLoneBot 与 NapCat

主要支持 AstrBot 的 OneBot v11（`aiocqhttp`）接入。LLoneBot 和 NapCat 都实现了这个协议，插件不需要分别选择协议端。这与 QQ 官方机器人的 `qq_official` 接口是不同的接入方式，后者没有做兼容验证。

PDF 直传使用两端共同支持的 `upload_private_file`（私聊）和 `upload_group_file`（群聊）接口，参数为 `user_id` / `group_id`、`file`、`name`。群聊发到当前群的文件区，私聊发给当前发送者；不会依赖普通消息接口对 `file` 消息段的扩展支持。

同机部署且没有启用 AstrBot 文件回调时，发送本地绝对路径。Windows 中文文件名和带空格的路径不需要手工改写。

分开部署时，有两种做法：让 AstrBot 与协议端在相同绝对路径读取同一份 PDF；或配置 AstrBot 的 `callback_api_base`，插件会使用 AstrBot 文件服务生成的 URL。这个 URL 必须能从 LLoneBot / NapCat 所在的机器或容器访问，不能把另一个容器的 `127.0.0.1` 当作 AstrBot 地址。

接口和参数已对照两端源码核对，并使用 AstrBot 的 OneBot 事件对象测试了私聊、群聊、账号路由和文件回调。没有将这些测试当作 LLoneBot、NapCat 两端真实 QQ 文件投递的验收；QQ 登录状态、临时会话、文件大小和协议端自身错误仍可能影响发送。文件接口报错会保留在 AstrBot 日志中，不会自动改为上传第三方网站。

接口参考：[LLoneBot 私聊文件接口](https://github.com/LLOneBot/LuckyLilliaBot/blob/main/src/onebot11/action/go-cqhttp/UploadPrivateFile.ts)、[LLoneBot 群文件接口](https://github.com/LLOneBot/LuckyLilliaBot/blob/main/src/onebot11/action/go-cqhttp/UploadGroupFile.ts)、[NapCat API 文档](https://napneko.github.io/onebot/api)。

## 使用

```text
/jm 123456
```

把示例数字替换成目标漫画的 ID。私聊可直接发送；群聊需要先开启群聊开关，并按 AstrBot 的唤醒规则触发指令。

同一时间处理一个下载或发送请求，忙碌时会提示稍后重试。已有 PDF 会直接复用。

## 文件位置

生成的 PDF 保存在 AstrBot 的 `data/jm_tmp` 下，路径随 AstrBot 数据目录一起迁移，支持 Windows 和 Linux。旧版的 `<ID>.pdf` 和 `[ID] 标题.pdf` 文件都可以复用。

临时图片目录在任务结束或失败后自动清理；合成后的 PDF 会保留，两种发送模式都一样。PDF 会持续占用磁盘，需要释放空间时可以在没有任务运行时手动清理。

插件配置保存在 AstrBot 的 `data/config/astrbot_plugin_jmcomic_config.json` 中。正常使用只需在界面保存配置。

## 从旧版升级

旧版写在代码里的 QQ 白名单不再使用。升级后默认关闭群聊、仅允许 AstrBot 管理员使用；需要开放时请在插件配置中调整。

仓库旧版默认返回网站链接，新安装仍使用这个默认值。之前的本机 PDF 直传版也可以使用相同代码，在配置里选“直接发送 PDF”即可。

## 常见情况

- 提示仅管理员可用：把使用者设为 AstrBot 管理员，或将使用权限改为“所有人”。
- 群里不能用：检查本插件的群聊开关，以及 AstrBot 自身的群聊和唤醒设置。
- 上传失败：检查运行 AstrBot 的设备能否连接 `tmpfiles.org`，具体错误见 AstrBot 日志。
- 生成了 PDF 但发不出去：检查消息平台的文件能力、协议端可见路径和文件大小限制。
- 没出现配置项：确认插件目录里有 `_conf_schema.json`，然后重载插件。

插件通过 [JMComic-Crawler-Python](https://github.com/hect0x7/JMComic-Crawler-Python) 下载图片。只处理你有权访问、保存和分享的内容。
