# -*- coding: utf-8 -*-
"""環境変数から数値の設定を読む。

GitHub Actions では未設定の Secret/変数が空文字で渡ってくる。空や変な値で監視全体が落ちないよう、
読めなければ既定値に戻し、範囲外は端に寄せる（どちらも1行ログに出す）。
"""
import os


def env_number(name, default, lo=None, hi=None, cast=int):
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = cast(raw)
    except ValueError:
        print("  [config] %s=%r は数値として読めないので既定値 %s を使う" % (name, raw, default))
        return default
    if value != value:  # NaN
        return default
    if lo is not None and value < lo:
        print("  [config] %s=%s は小さすぎるので %s にする" % (name, value, lo))
        value = lo
    if hi is not None and value > hi:
        print("  [config] %s=%s は大きすぎるので %s にする" % (name, value, hi))
        value = hi
    return value
