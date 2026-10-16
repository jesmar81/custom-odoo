import os

from . import wizard


def post_init_hook(env):
    """Crear o actualizar los objetos SQL del módulo (función del wizard)."""
    sql_file_path = os.path.join(os.path.dirname(__file__), 'sql', 'procedimiento.sql')
    with open(sql_file_path, encoding='utf-8') as sql_file:
        env.cr.execute(sql_file.read())
