// Copyright (c) 2026 刘晨宇 <https://github.com/klwy9768/stories>
// SPDX-License-Identifier: MIT
/// 客户端访问令牌：请填入你自己服务器的 STORIES_APP_TOKEN
/// 留空时服务端会拒绝所有请求（这是刻意的失败安全设计）
const String kAppToken = '';

/// 旧令牌（令牌轮换时用于识别老设备，可留空）
const String kOldToken = '';

String appToken = kAppToken;
