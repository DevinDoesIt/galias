#! /usr/bin/env python
"""
Copyright 2014 Trevor Ellermann

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

from collections import defaultdict
from optparse import OptionParser
from optparse import OptionGroup
import argparse
import os.path
import ConfigParser
from apiclient.errors import HttpError
from apiclient.discovery import build
import httplib2
from oauth2client.client import flow_from_clientsecrets
from oauth2client.file import Storage
from oauth2client import tools
from oauth2client.tools import run_flow
from oauth2client.client import AccessTokenRefreshError
import sys
import random
from retrying import retry
import pprint

# CLIENT_SECRETS, name of a file containing the OAuth 2.0 information for this
# application, including client_id and client_secret, which are found
# on the API Access tab on the Google APIs
# Console <http://code.google.com/apis/console>
CLIENT_SECRETS = 'client_secrets.json'

# Helpful message to display in the browser if the CLIENT_SECRETS file
# is missing.
MISSING_CLIENT_SECRETS_MESSAGE = """
WARNING: Please configure OAuth 2.0

To make this sample run you will need to populate the client_secrets.json file
found at:

   %s

with information from the APIs Console <https://code.google.com/apis/console>.

""" % os.path.join(os.path.dirname(__file__), CLIENT_SECRETS)

def retry_if_http_error(exception):
    """Return True if we should retry  False otherwise"""
    return isinstance(exception, HttpError)

# Implement backoff in case of API rate errors
@retry(wait_exponential_multiplier=1000,
       wait_exponential_max=10000,
       retry_on_exception=retry_if_http_error,
       wrap_exception=False)
def execute_with_backoff(request):
    response = request.execute()
    return response


def get_all_groups(admin_service, domain=None, user=None):
    group_service = admin_service.groups()
    all_groups = []
    request = group_service.list(domain=domain, userKey=user)
    while (request is not None):
        response = execute_with_backoff(request)
        all_groups.extend(response['groups'])
        request = group_service.list_next(request, response)
    return all_groups


def get_group(admin_service, group_name):
    group_service = admin_service.groups()
    request = group_service.get(groupKey=group_name)
    response = execute_with_backoff(request)
    return response


def get_group_members(admin_service, group_email):
    member_service = admin_service.members()
    members = []
    request = member_service.list(groupKey=group_email)
    while (request is not None):
        response = execute_with_backoff(request)
        try:
            members.extend(response['members'])
        except KeyError:
            return None
        request = member_service.list_next(request, response)
    return members

def create_group(group_service, group_id, group_name, description, email_permission):
    return group_service.CreateGroup(group_id, group_name, description, email_permission)

def remove_group(group_service, group_email):
    return group_service.DeleteGroup(group_email)

def add_group_member(group_service, group_email, email_address):
    return group_service.AddMemberToGroup(email_address, group_email)

def remove_group_member(group_service, email_address, group_email):
    return group_service.RemoveMemberFromGroup(email_address, group_email)

def is_group_member(group_service, email_address, group_email):
    return group_service.IsMember(email_address, group_email)


def print_all_members(admin_service, domain):
    groups = get_all_groups(admin_service, domain)
    for group in groups:
        print_group(admin_service, group)


def list_group(admin_service, group_email):
    group = get_group(admin_service, group_email)
    print_group(admin_service, group)


def print_members(admin_service, group_email):
    gid = ""
    members = get_group_members(admin_service, group_email)
    if members:
        for user in members:
            try:
                print gid + "->", user['email']
                gid = group_email + " "
            except KeyError:
                continue
    else:
        print gid + "-> Empty"


def print_memberships(address, groups):
    # Takes a string and a list of groups
    print address + ":"
    for group in groups:
        print "  " + group
    print


def retrieve_list_memberships(admin_service, domain, userlist):
    users = defaultdict(list)
    if len(userlist) == 0:
        groups = get_all_groups(admin_service, domain)
        for group in groups:
            members = get_group_members(admin_service, group['email'])
            if members is not None:
                for user in members:
                    try:
                        users[user["email"]].append(group['email'])
                    except KeyError:
                        continue
    else:
        for user in userlist:
            groups = get_all_groups(admin_service, domain, user)
            for group in groups:
                users[user].append(group['email'])
    return users


def print_list_memberships(admin_service, domain, users):
    user_memberships = retrieve_list_memberships(admin_service, domain, users)
    if len(users) == 0:
        userlist = sorted(user_memberships)
    else:
        userlist = users

    for user in userlist:
        print_memberships(user, user_memberships[user])

def add_to_alias(group_service, alias, address):
    try:
        group = get_group(group_service, alias)
    except Exception, e:
        if e.reason == "EntityDoesNotExist":
            print "New Alias " + alias
            name = "Alias " + alias
            create_group(group_service, alias, name, "", "Anyone")
            group = get_group(group_service, alias)
        else:
            raise e

    add_group_member(group_service, alias, address)
    print "Added"
    print "Current status of alias"
    print_group(group_service, group)

def delete_from_alias(group_service, alias, address):
    try:
        group = get_group(group_service, alias)
    except Exception, e:
        if e.reason == "EntityDoesNotExist":
            print "Invalid Alias " + alias
        else:
            raise e
        return

    if not is_group_member(group_service, address, alias):
        print "*" * 70
        print "* " + address + " is not in " + alias
        print "*" * 70
    else:
        remove_group_member(group_service, address, alias)
        print "Deleted"

    members = get_group_members(group_service, alias)
    if not members:
        remove_group(group_service, alias)
        print "Alias empty, removing alias"
    else:
        print "Current status of alias"
        print_group(group_service, group)


def print_group(admin_service, group):
    gid = group['email']
    print('%s' % (gid)),
    print_members(admin_service, gid)

def main(argv):
    config_username = ""
    config_password = ""
    config_domain = ""
    if os.path.isfile("galias.ini"):
        Config = ConfigParser.ConfigParser()
        Config.read("galias.ini")
        config_username = Config.get("galias", "username")
        config_password = Config.get("galias", "password")
        config_domain = Config.get("galias", "domain")

    usage = "usage: %prog [options] COMMAND \n\
        \nPossible COMANDS are: \
        \n    listall - List all aliases \
        \n    list <alias> - list the specified alias \
        \n    list_memberships [addresses] - list alias memberships for a list of addresses (or all if addresses are missing) \
        \n    add <alias> <destination> - add the <destination> to the <alias> \
        \n    delete <alias> <destination> - delete the <destination> from the <alias> \
        "
    parser = OptionParser(usage)

    parser.add_option('-u', '--username', default=config_username)
    parser.add_option('-p', '--password', default=config_password)
    parser.add_option('-d', '--domain', default=config_domain)
    parser.add_option('--auth_host_name', default='localhost',
                      help='Hostname when running a local web server.')
    parser.add_option('--noauth_local_webserver', action='store_true',
                      default=False, help='Do not run a local web server.')
    parser.add_option('--auth_host_port', default=[8080, 8090], type=int,
                      nargs='*', help='Port web server should listen on.')
    parser.add_option('--logging_level', default='ERROR',
                      choices=['DEBUG', 'INFO', 'WARNING', 'ERROR',
                               'CRITICAL'],
                      help='Set the logging level of detail.')
    group = OptionGroup(parser, "Dangerous Options",
                        "Caution: use these options at your own risk.  "
                        "It is believed that some of them bite.")

    options, args = parser.parse_args()

    if len(args) < 1:
        parser.error("incorrect number of arguments")
    else:
        command = args[0]

    if not options.domain:
        options.domain = raw_input("Google apps domain name: ")

    # Set up a Flow object to be used if we need to authenticate.
    scope = ("https://www.googleapis.com/auth/admin.directory.group"
             " "
             "https://www.googleapis.com/auth/admin.directory.group.member"
             " "
             "https://www.googleapis.com/auth/apps.groups.settings")
    FLOW = flow_from_clientsecrets(CLIENT_SECRETS,
                                   scope=scope,
                                   message=MISSING_CLIENT_SECRETS_MESSAGE)
    # Create an httplib2.Http object to handle our HTTP requests
    http = httplib2.Http()
    storage = Storage('credentials.dat')
    credentials = storage.get()

    if credentials is None or credentials.invalid:
        print 'invalid credentials'
        # Save the credentials in storage to be used in subsequent runs.
        credentials = run_flow(FLOW, storage, flags=options, http=http)

    # Authorize with our good Credentdials
    http = credentials.authorize(http)

    admin_service = build('admin', 'directory_v1', http=http)
    group_settings_service = build('groupssettings', 'v1', http=http)

    # COMMANDS

    if command == "listall":
        print_all_members(admin_service, config_domain)
    elif command == "list":
        print "listing alias", args[1]
        list_group(admin_service, args[1])
    elif command == "list_memberships":
        print "listing alias memberships"
        if len(args) == 1:
            print_list_memberships(admin_service, config_domain, [])
        else:
            print_list_memberships(admin_service, config_domain, args[1:])
    elif command == "add":
        print "%s add %s" % (args[1], args[2])
        add_to_alias(group_service, args[1], args[2])
    elif command == "delete":
        print "%s delete %s" % (args[1], args[2])
        delete_from_alias(group_service, args[1], args[2])
    else:
        print "Unknown command"


if __name__ == '__main__':
    main(sys.argv)
