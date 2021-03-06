import logging
import json

import ldap as ldap_module
from schematics.exceptions import ModelValidationError, ModelConversionError
from schematics.models import Model
from schematics.types import StringType
from schematics.types.compound import DictType, ListType

import hqmanager.assignment.sql_driver
from hqlib.sql.models import UserAssignment
from hqmanager.assignment.driver import AssignmentMissingDBConnectionException

class AssignmentDriver(hqmanager.assignment.sql_driver.AssignmentDriver):
    def __init__(self):
        super(AssignmentDriver, self).__init__()
        self.logger = logging.getLogger("hq.manager.assignment.ldap")
        self.database = None
        self.ldap = None

    def db_connections(self, **kwargs):
        if 'database' not in kwargs:
            raise AssignmentMissingDBConnectionException("Missing sql connection")

        if 'ldap' not in kwargs:
            raise AssignmentMissingDBConnectionException("Missing ldap connection")

        if kwargs['ldap'] is None:
            raise AssignmentMissingDBConnectionException("Missing ldap connection is None")

        self.database = kwargs['database']
        self.ldap = kwargs['ldap']

    def validate_config(self, config):

        class ConfigValidator(Model):
            mapping = DictType(ListType(StringType()), required=True)

        try:
            self.config = ConfigValidator(config, strict=False)
        except ModelConversionError as e:
            self.logger.error("Could not create config for assignment LDAP driver " + json.dumps(e.message))
            return False

        try:
            self.config.validate()
        except ModelValidationError as e:
            self.logger.error("Could not validate config for assignment LDAP driver " + json.dumps(e.message))
            return False

        return True

    def get_assignment_id(self, username):
        with self.database.session() as session:
            assignment = session.query(UserAssignment).filter(UserAssignment.username == username).first()
            return assignment.id

    # LDAP assignment always needs to exist so if it doesn't create it
    def has_assignment(self, username):
        with self.database.session() as session:
            assignment = session.query(UserAssignment).filter(UserAssignment.username == username).first()
        if assignment is None:
            self.create_assignment(username)
        return True

    # only create assignment if user is in LDAP
    def create_assignment(self, username):
        connection = self.ldap.connection_as(self.ldap.bind_username, self.ldap.bind_password)

        criteria = "(&(samaccountname=" + username + "))"
        attributes = ['displayName']
        result = connection.search_s(self.ldap.base_dn, ldap_module.SCOPE_SUBTREE, criteria, attributes)[0][0]
        connection.unbind()

        if result is None:
            self.logger.warning("Trying to create assignment for user " + username + " but they are not in ldap.")
            return

        with self.database.session() as session:
            assignment = UserAssignment(username=username)
            session.add(assignment)
            session.commit()
            session.refresh(assignment)

    def remove_permission(self, username, permission):
        pass

    def has_permission_user(self, username, permission, exact=False):
        perms = self.get_permissions(username)

        for perm in perms:
            perm_split = perm.split(".")
            permission_split = permission.split(".")

            matches = True

            if len(permission_split) >= len(perm_split):
                for i in range(0, len(perm_split)):
                    if perm_split[i] == permission_split[i]:
                        continue
                    elif perm_split[i] == "*" and exact is False:
                        continue
                    else:
                        matches = False
                        break
            else:
                matches = False

            if matches is False:
                continue

            return True

        return False

    def has_permission_token(self, token, permission, exact=False):
        return self.has_permission_user(self.get_username_from_token(token), permission, exact)

    def add_permission(self, username, permission):
        pass

    def get_permissions(self, username):

        permissions = []

        connection = self.ldap.connection_as(self.ldap.bind_username, self.ldap.bind_password)

        criteria = "(&(samaccountname=" + username + "))"
        attributes = ['memberOf']
        result = connection.search_s(self.ldap.base_dn, ldap_module.SCOPE_SUBTREE, criteria,
                                     attributes)[0][1]['memberOf']
        connection.unbind()

        for group in result:
            group = group.replace(self.ldap.base_dn, "")[:-1]
            if group not in self.config.mapping:
                continue
            else:
                for perm in self.config.mapping[group]:
                    permissions.append(perm)

        return permissions
