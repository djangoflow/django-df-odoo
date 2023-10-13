# Django DF ODOO

Module for integrating django and odoo

## Installation:

- Install the package

```
pip install django-df-odoo
```


- Include default `INSTALLED_APPS` from `df_odoo.defaults` to your `settings.py`

```python
from df_odoo.defaults import DF_ODOO_INSTALLED_APPS

INSTALLED_APPS = [
    ...
    *DF_ODOO_INSTALLED_APPS,
    ...
]

```


## Development

Installing dev requirements:

```
pip install -e .[test]
```

Installing pre-commit hook:

```
pre-commit install
```

Running tests:

```
pytest
```
