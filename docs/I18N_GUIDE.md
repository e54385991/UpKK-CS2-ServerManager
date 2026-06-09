# 国际化支持文档 / Internationalization Documentation

[中文](#中文) | [English](#english)

---

## 中文

### 概述

CS2 服务器管理器现已支持完整的国际化（i18n），提供中英文双语界面。

### 功能特性

- ✅ **自动语言检测**：首次访问时根据浏览器语言自动选择界面语言
- ✅ **便捷切换**：导航栏提供语言切换下拉菜单
- ✅ **持久化存储**：语言偏好保存在浏览器本地存储中
- ✅ **实时切换**：无需刷新页面即可切换语言
- ✅ **全面覆盖**：超过 250 个界面字符串已翻译

### 使用方法

#### 自动语言检测

首次访问时，系统会自动检测您的浏览器语言：
- 浏览器语言为中文 → 自动使用中文界面
- 浏览器语言非中文 → 自动使用英文界面

#### 手动切换语言

1. 在页面顶部导航栏找到语言切换按钮（地球图标）
2. 点击下拉菜单
3. 选择 "中文" 或 "English"
4. 界面将立即切换到所选语言

### 支持的语言

- 🇺🇸 **English (en-US)** - 英语（美国）
- 🇨🇳 **中文 (zh-CN)** - 简体中文

### 已翻译的页面

- ✅ 导航栏
- ✅ 首页
- ✅ 登录页
- ✅ 注册页
- ✅ 服务器管理页
- ✅ 服务器详情页
- ✅ 设置向导
- ✅ 通用界面元素

### 技术实现

#### 翻译文件位置
```
static/locales/
├── en-US.json  # 英文翻译
└── zh-CN.json  # 中文翻译
```

#### i18n 库
- 位置：`static/js/i18n.js`
- 特性：轻量级、零依赖、自动检测、实时切换

#### 在 HTML 中使用

```html
<!-- 翻译文本内容 -->
<span data-i18n="nav.home">Home</span>

<!-- 翻译占位符 -->
<input data-i18n-placeholder="login.username" placeholder="Username">

<!-- 翻译标题 -->
<button data-i18n-title="common.save" title="Save">保存</button>

<!-- 翻译 HTML 内容 -->
<div data-i18n-html="home.features.fastAsync.description"></div>
```

#### 在 JavaScript 中使用

```javascript
// 获取翻译
const text = window.i18n.t('servers.title');

// 切换语言
window.i18n.setLocale('zh-CN');

// 获取当前语言
const currentLocale = window.i18n.getLocale();
```

### 开发者指南

#### 添加新的翻译

1. 在 `static/locales/en-US.json` 中添加英文翻译
2. 在 `static/locales/zh-CN.json` 中添加对应的中文翻译
3. 在 HTML 模板中使用 `data-i18n` 属性

示例：
```json
// en-US.json
{
  "myFeature": {
    "title": "My Feature",
    "description": "This is a new feature"
  }
}

// zh-CN.json
{
  "myFeature": {
    "title": "我的功能",
    "description": "这是一个新功能"
  }
}
```

```html
<!-- HTML -->
<h1 data-i18n="myFeature.title">My Feature</h1>
<p data-i18n="myFeature.description">This is a new feature</p>
```

#### 添加新语言

1. 创建新的翻译文件：`static/locales/{语言代码}.json`
2. 修改 `static/js/i18n.js`：
   - 在 `supportedLocales` 数组中添加新语言代码
   - 在 `getLocaleDisplayName()` 方法中添加语言显示名称
3. 在 `templates/base.html` 的语言切换器中添加新选项

### 故障排除

**问题：切换语言后部分文本未翻译**
- 原因：该文本可能是动态生成的
- 解决：使用 `window.i18n.t()` 在 JavaScript 中获取翻译

**问题：浏览器语言检测不正确**
- 原因：浏览器语言设置可能不标准
- 解决：手动在导航栏切换语言，系统会记住您的选择

**问题：翻译显示为键名**
- 原因：翻译文件中缺少该键
- 解决：在翻译文件中添加对应的翻译

---

## English

### Overview

CS2 Server Manager now supports full internationalization (i18n) with bilingual interface in Chinese and English.

### Features

- ✅ **Automatic Language Detection**: Automatically selects interface language based on browser language on first visit
- ✅ **Easy Switching**: Language switcher dropdown in navigation bar
- ✅ **Persistent Storage**: Language preference saved in browser localStorage
- ✅ **Real-time Switching**: Switch languages without page reload
- ✅ **Comprehensive Coverage**: Over 250 UI strings translated

### How to Use

#### Automatic Language Detection

On first visit, the system automatically detects your browser language:
- Browser language is Chinese → Automatically use Chinese interface
- Browser language is not Chinese → Automatically use English interface

#### Manual Language Switching

1. Find the language switcher button (globe icon) in the top navigation bar
2. Click the dropdown menu
3. Select "中文" or "English"
4. The interface will immediately switch to the selected language

### Supported Languages

- 🇺🇸 **English (en-US)** - English (United States)
- 🇨🇳 **中文 (zh-CN)** - Simplified Chinese

### Translated Pages

- ✅ Navigation Bar
- ✅ Home Page
- ✅ Login Page
- ✅ Registration Page
- ✅ Server Management Page
- ✅ Server Details Page
- ✅ Setup Wizard
- ✅ Common UI Elements

### Technical Implementation

#### Translation Files Location
```
static/locales/
├── en-US.json  # English translations
└── zh-CN.json  # Chinese translations
```

#### i18n Library
- Location: `static/js/i18n.js`
- Features: Lightweight, zero dependencies, auto-detection, real-time switching

#### Usage in HTML

```html
<!-- Translate text content -->
<span data-i18n="nav.home">Home</span>

<!-- Translate placeholder -->
<input data-i18n-placeholder="login.username" placeholder="Username">

<!-- Translate title -->
<button data-i18n-title="common.save" title="Save">Save</button>

<!-- Translate HTML content -->
<div data-i18n-html="home.features.fastAsync.description"></div>
```

#### Usage in JavaScript

```javascript
// Get translation
const text = window.i18n.t('servers.title');

// Switch language
window.i18n.setLocale('zh-CN');

// Get current language
const currentLocale = window.i18n.getLocale();
```

### Developer Guide

#### Adding New Translations

1. Add English translation in `static/locales/en-US.json`
2. Add corresponding Chinese translation in `static/locales/zh-CN.json`
3. Use `data-i18n` attribute in HTML template

Example:
```json
// en-US.json
{
  "myFeature": {
    "title": "My Feature",
    "description": "This is a new feature"
  }
}

// zh-CN.json
{
  "myFeature": {
    "title": "我的功能",
    "description": "这是一个新功能"
  }
}
```

```html
<!-- HTML -->
<h1 data-i18n="myFeature.title">My Feature</h1>
<p data-i18n="myFeature.description">This is a new feature</p>
```

#### Adding a New Language

1. Create new translation file: `static/locales/{language-code}.json`
2. Modify `static/js/i18n.js`:
   - Add new language code to `supportedLocales` array
   - Add language display name in `getLocaleDisplayName()` method
3. Add new option to language switcher in `templates/base.html`

### Troubleshooting

**Issue: Some text not translated after switching language**
- Cause: The text might be dynamically generated
- Solution: Use `window.i18n.t()` to get translations in JavaScript

**Issue: Browser language detection is incorrect**
- Cause: Browser language settings might be non-standard
- Solution: Manually switch language in navigation bar, system will remember your choice

**Issue: Translation displays as key name**
- Cause: Translation key missing in translation file
- Solution: Add corresponding translation in translation files

---

## 贡献 / Contributing

欢迎贡献新的翻译或改进现有翻译！请提交 Pull Request。

Contributions for new translations or improvements to existing translations are welcome! Please submit a Pull Request.

## 许可 / License

与主项目相同 / Same as main project
