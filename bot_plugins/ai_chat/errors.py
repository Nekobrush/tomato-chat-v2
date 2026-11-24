"""插件内统一的异常类型。

单独成模块是为了让 `providers`、`model`、`pipeline` 都能引用，而不产生循环导入。
"""

from __future__ import annotations


class LLMError(RuntimeError):
    """模型配置、网络或生成过程中的可预期错误（会被转成给用户的提示）。"""
