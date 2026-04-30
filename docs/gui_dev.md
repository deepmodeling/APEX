# APEX GUI 开发说明

本文件面向后续维护 `apex gui` 前端（Dash）功能，记录当前实现位置、数据流和扩展约定。

## 1. 代码位置

- 主入口与前端实现：`apex/gui.py`
- Account 配置读写：`apex/account.py`
- Submit 模板目录：`apex/default_config/{lammps,vasp,abacus}/`

当前 GUI 使用 Dash，前端布局和后端回调都在同一个 Python 文件中（`ApexGuiApp` 类）。

## 2. Tab 结构

在 `ApexGuiApp._build_layout()` 中定义了 4 个页签：

1. `Submit`
2. `Log`
3. `Advanced`
4. `Account`

对应构建函数：

- `_build_submit_tab`
- `_build_manage_tab`
- `_build_advanced_tab`
- `_build_account_tab`

## 3. Submit 页面（核心）

### 3.1 模板与数据来源

`Submit` 页面不是手写 JSON，而是按 profile 模板拼接得到：

- `param_structure.json`
- `param_interaction/param_interaction.json`
- `param_relax.json`
- `param_props.json`

拼接函数：`_load_profile_param_template(profile)`。

`global.json` 来源：`_load_profile_global(profile)`。

### 3.2 用户操作与按钮语义

Submit 页底部按钮：

- `Reset`：重新按当前表单状态生成 `param.json` 编辑区内容。
- `Apply`：把用户当前编辑内容写入 `global.json`/`param.json`，并写入 interaction 相关文件（如 `vasp_input/INCAR`）。
- `Submit`：在后台执行：
  `nohup apex submit param.json -c global.json > apex.log 2>&1 &`

Submit 页现在区分两类上传：

- `上传结构`：结构文件默认保存到当前 `Workdir/confs/`。若 `confs/` 不存在会自动创建。
- `上传文件`：普通文件直接保存到当前 `Working Directory`。

上传结果显示在 `Command Output`。

`structures` 不再只靠手改 `param.json`，而是通过下拉框从当前 `Workdir` 中选择结构目录/结构路径，避免误写成 `.`。

### 3.3 提交前日志冲突确认

`Submit` 时会检查当前目录是否已有 `apex.log`。

- 若存在：弹 `ConfirmDialog` 二次确认。
- 用户确认后才会真正重新提交。

### 3.4 interaction 编辑规则

- `lammps`：显示 `interaction.type` 和 `interaction.model` 文件选择框。
- `vasp`：不再手选 `interaction.type`。GUI 会从所选结构目录中的 `POSCAR` 自动读取元素顺序，并在 `Workdir` 中优先检索 `vasp_input/` 下后缀匹配的 POTCAR 文件；缺失元素会用灰字提示“请提交对应元素的POTCAR”。
- `abacus`：不再手选 `interaction.type`。GUI 会从所选结构目录中的 `POSCAR` 自动读取元素顺序，并在 `Workdir` 中优先检索 `abacus_input/` 下前缀匹配的赝势/轨道文件；缺失项会用灰字提示。

右侧 Advanced Setting 中：

- `vasp` 使用 `interaction.incar`
- `abacus` 使用 `interaction.input`

若对应文件不存在，`Apply/Submit` 时会按 profile 默认模板自动创建：

- `vasp_input/INCAR`
- `abacus_input/INPUT`

### 3.5 Properties 勾选行为

`param.json` 中 `properties` 只保留勾选项；未勾选项不写入输出 JSON。

## 4. Log 页面

`Log` 页面用于查看 `apex.log`：

- 手动刷新按钮
- 3 秒自动刷新
- 显示日志尾部内容（tail）

日志读取函数：`_read_log_tail()`。

## 5. Advanced 页面

支持执行命令尾参数（会调用 `python -m apex ...`）。

- 例如输入：`submit param_joint.json -c global.json`
- `gui` 和 `report` 被禁止，避免嵌套启动 Dash。

## 6. Account 页面

### 6.1 目标

GUI 中提供对 `apex account` 存储的可视化覆盖编辑。

### 6.2 当前字段

- `email`
- `program_id`
- `password`（仅覆盖，不回显）

### 6.3 安全策略

- 页面不会展示明文密码，只显示“已设置/未设置”。
- 密码输入框保存后会清空。
- 底层文件仍由 `save_account_config()` 写入。

### 6.4 相关函数

在 `apex/gui.py`：

- `_load_account_state`
- `_render_account_summary`
- `_save_account_overwrite`
- 回调：`_handle_account`

在 `apex/account.py`：

- `load_account_config`
- `save_account_config`
- `mask_sensitive_config`

## 7. 关键回调清单（apex/gui.py）

- `_sync_profile_defaults`：profile 切换时同步默认值与控件状态。
- `_update_interaction_table`：动态增删行与 profile 切换重置。
- `_generate_param_editor`：根据 UI 状态生成 `param.json` 文本。
- `_handle_command`：处理 `Apply/Submit/Confirm/Advanced` 动作。
- `_handle_structure_upload`：把结构文件上传到当前 `Workdir/confs/`。
- `_handle_file_upload`：把普通文件上传到当前 `Workdir`。
- `_handle_account`：处理 Account 刷新与覆盖保存。
- `_update_manage_log`：更新 `apex.log` 展示。

## 8. 扩展建议

1. 若 Submit 继续变复杂，建议把 `apex/gui.py` 拆分为：
   - `apex/gui/layout.py`
   - `apex/gui/callbacks_submit.py`
   - `apex/gui/callbacks_account.py`
2. 为每个回调增加最小单元测试，特别是：
   - `properties` 过滤逻辑
   - `apex.log` 重提确认逻辑
   - Account 密码不回显逻辑
3. 新增 profile 时，必须同步补齐 `default_config/<profile>/` 四个 param 子模板。

## 9. 快速联调

```bash
apex gui --no-browser
```

默认地址：`http://127.0.0.1:8060/`

若改动了 GUI，建议至少执行：

```bash
python -m py_compile apex/gui.py tests/test_gui_submit_builder.py
python -m unittest tests.test_gui_cli tests.test_gui_submit_builder -v
```
