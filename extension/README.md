# Cleon Jupyter Extension

JupyterLab extension for manipulating notebook cells from the kernel.

## Installation

```bash
pip install cleon-jupyter-extension
```

Then restart JupyterLab.

## Usage

```python
from cleon_cell_control import insert_and_run, insert_cell, create_insert_button

# Insert code into a new cell below and execute it
insert_and_run("print('Hello!')")

# Just insert a cell without running
insert_cell("# My new cell", position="below")

# Create a button that inserts and runs code when clicked
create_insert_button("import pandas as pd")
```

## API

- `insert_and_run(code, cell_type="code")` - Insert cell below and execute
- `insert_cell(code, position="below", cell_type="code")` - Insert cell
- `replace_cell(code)` - Replace current cell content
- `execute_cell()` - Execute current cell
- `create_insert_button(code, label)` - Create clickable button

## Development

```bash
cd extension
./install.sh

# Or manually:
npm install
npm run build:prod
pip install -e .
```
