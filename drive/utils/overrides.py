import frappe

from drive.api.permissions import get_teams


def common_filters(func):
    def decorator(user):
        user = user or frappe.session.user
        if user == "Administrator" or "Drive Admin" in frappe.get_roles():
            return ""
        return func(frappe.db.escape(user))

    return decorator


@common_filters
def filter_drive_file(user):
    return f"""(`tabDrive File`.`owner` = {user})"""


@common_filters
def filter_drive_permission(user):
    return f"""(`tabDrive Permission`.`owner` = {user} or `tabDrive Permission`.user = {user})"""


@common_filters
def filter_drive_team(user):
    teams = get_teams(user)
    if teams:
        teams = ", ".join(frappe.db.escape(team) for team in teams)
        return f"""(`tabDrive Team`.name in ({teams}))"""


@common_filters
def filter_drive_document(user):
    return f"""(`tabDrive Document`.`owner` = {user})"""


@common_filters
def filter_drive_comment(user):
    return f"""(`tabDrive Comment`.`owner` = {user})"""


@common_filters
def filter_drive_favourite(user):
    user = user or frappe.session.user
    if user == "Administrator":
        return ""
    return f"""(`tabDrive Favourite`.`user` = {user})"""


@common_filters
def filter_drive_recent(user):
    return f"""(`tabDrive Entity Log`.`user` = {user})"""


@common_filters
def filter_drive_notif(user):
    return f"(`tabDrive Notification`.to_user = {user} or `tabDrive Notification`.from_user = {user})"


ERROR_LOG_TITLE_MAX_LENGTH = 140


def truncate_error_log_title(doc, method=None):
    """Error Log's method (Title) field is a Data field capped at 140 chars; a
    long exception string would make the insert fail and drop the whole log.
    Runs as a doc_events before_insert hook so it applies on every site
    (localhost, staging, production) without patching the frappe app."""
    if doc.method and len(doc.method) > ERROR_LOG_TITLE_MAX_LENGTH:
        doc.method = doc.method[:ERROR_LOG_TITLE_MAX_LENGTH]
