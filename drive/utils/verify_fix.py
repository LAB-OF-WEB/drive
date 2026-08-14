import frappe
from drive.utils.files import FileManager

m = FileManager()
ent = frappe.get_doc('Drive File', '4rm8u2k2op')
print('JAFZA path:', ent.path)
print('File exists on disk:', (m.site_folder / ent.path).is_file())
buf = m.get_file(ent)
print('JAFZA download:', len(buf.getvalue()), 'bytes')

rows = frappe.get_all('Drive File', filters={'is_active': 1, 'is_group': 0}, fields=['name'])
fail = 0
for r in rows:
    f = frappe.get_value('Drive File', r['name'], ['path'], as_dict=1)
    try:
        buf = m.get_file(f)
    except Exception as e:
        fail += 1
        print('FAIL:', r['name'], e)
print('Total active: %d, Failures: %d' % (len(rows), fail))
