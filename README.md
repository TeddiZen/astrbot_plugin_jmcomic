# astrbot_plugin_jmcomic

发送 `/jm 漫画ID`，下载漫画并合成 PDF。支持直接发送 PDF，也可以上传到 tmpfiles.org 后返回下载链接。

## 安装

在 AstrBot 插件页面点击右下角 `+`，选择“从链接安装”，填入：

```text
https://github.com/air041001/astrbot_plugin_jmcomic
```

安装后点插件卡片上的齿轮进行配置。

## 配置

| 配置项 | 默认值 | 可选项 |
| --- | --- | --- |
| 允许在群聊中使用 | 关闭 | 开启后可在群聊使用 |
| 使用权限 | 仅管理员 | 仅管理员、所有人 |
| 发送方式 | 下载链接 | 下载链接、直接发送 PDF |

管理员指 AstrBot 后台设置的全局管理员。选择“所有人”后，仍需符合 AstrBot 自身的访问控制和唤醒设置。

直接发送 PDF 支持通过 OneBot v11 接入的 LLoneBot、NapCat。两端分开部署时，需要共享文件路径，或配置协议端能访问的 AstrBot 文件回调地址 `callback_api_base`。

下载链接模式会把 PDF 上传到 tmpfiles.org，返回的链接有时效性。

## 使用

```text
/jm 123456
```

把数字替换成漫画 ID。群聊需要先开启群聊开关，再按 AstrBot 的唤醒方式发送指令。

生成的 PDF 保存在 `data/jm_tmp`，已有文件会直接复用。临时图片在处理结束后清理，PDF 可以在需要释放空间时手动删除。
