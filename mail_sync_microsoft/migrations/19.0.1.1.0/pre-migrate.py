# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""19.0.1.1.0: Odoo 19 no longer allows a stored field called ``display_name``.

The folder name moves to ``mail_sync_folder.name`` and the Graph display name of a mailbox to
``mail_sync_mailbox.graph_display_name``. Renaming the columns keeps the data.
"""

RENAMES = (
    ("mail_sync_folder", "display_name", "name"),
    ("mail_sync_mailbox", "display_name", "graph_display_name"),
)


def _has_column(cr, table, column):
    cr.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_name = %s AND column_name = %s",
        (table, column),
    )
    return bool(cr.fetchone())


def migrate(cr, version):
    for table, old, new in RENAMES:
        if _has_column(cr, table, old) and not _has_column(cr, table, new):
            # Identifiers come from the constant list above, not from user input.
            cr.execute(f'ALTER TABLE "{table}" RENAME COLUMN "{old}" TO "{new}"')
