import os


def migrate(cr, version):
    if not version:
        return
    module_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sql_file_path = os.path.join(module_root, 'sql', 'procedimiento.sql')
    with open(sql_file_path, encoding='utf-8') as sql_file:
        cr.execute(sql_file.read())
