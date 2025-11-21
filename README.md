[![PyPI version](https://img.shields.io/pypi/v/cleon.svg)](https://pypi.org/project/cleon/)
[![PyPI downloads](https://img.shields.io/pypi/dm/cleon.svg)](https://pypistats.org/packages/cleon)
[![Python versions](https://img.shields.io/pypi/pyversions/cleon.svg)](https://pypi.org/project/cleon/)
[![License](https://img.shields.io/pypi/l/cleon.svg)](https://pypi.org/project/cleon/)

# Cleon

<img src="https://raw.githubusercontent.com/madhavajay/cleon/main/img/cleon.png" alt="Cleon logo" style="max-height:300px;">

Cleon is a python library for jupyter which wraps AI session based agents like Codex, Claude and Gemini.

## Features
- low friction usage with configurable prefixes that trigger prompts in code cells:
`@ hi codex`
`~ hi claude`
`> hi gemini` <- coming soon

## Installation
`pip install cleon`

## Codex
- Make sure your codex is already authed.

## Claude Code
- Install Claude Code
- Run `cleon.login()`

## Options
- cleon.status()
- cleon.sessions()
- cleon.resume()
- cleon.stop()
- cleon.mode("learn")
- cleon.mode("do")
