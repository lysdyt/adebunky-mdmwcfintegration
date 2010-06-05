#!/usr/bin/env python
#
# Copyright (C) 2008, 2009 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


# This module is used for version 2 of the Google Data APIs.


"""Provides a client to interact with Google Data API servers.

This module is used for version 2 of the Google Data APIs. The primary class
in this module is GDClient.

  GDClient: handles auth and CRUD operations when communicating with servers.
  GDataClient: deprecated client for version one services. Will be removed.
"""


__author__ = 'j.s@google.com (Jeff Scudder)'


import re
import atom.client
import atom.core
import atom.http_core
import gdata.gauth
import gdata.data


# Old imports
import gdata.service
import urllib
import urlparse
import gdata.auth
import atom


class Error(Exception):
  pass


class RequestError(Error):
  status = None
  reason = None
  body = None
  headers = None


class RedirectError(RequestError):
  pass


class CaptchaChallenge(RequestError):
  captcha_url = None
  captcha_token = None


class ClientLoginTokenMissing(Error):
  pass


class MissingOAuthParameters(Error):
  pass


class ClientLoginFailed(RequestError):
  pass


class UnableToUpgradeToken(RequestError):
  pass


class Unauthorized(Error):
  pass


class BadAuthenticationServiceURL(RedirectError):
  pass


class BadAuthentication(RequestError):
  pass


def error_from_response(message, http_response, error_class, response_body=None):
  """Creates a new exception and sets the HTTP information in the error.
  
  Args:
   message: str human readable message to be displayed if the exception is
            not caught.
   http_response: The response from the server, contains error information.
   error_class: The exception to be instantiated and populated with
                information from the http_response
   response_body: str (optional) specify if the response has already been read
                  from the http_response object.
  """
  if response_body is None:
    body = http_response.read()
  else:
    body = response_body
  error = error_class('%s: %i, %s' % (message, http_response.status, body))
  error.status = http_response.status
  error.reason = http_response.reason
  error.body = body
  error.headers = http_response.getheaders()
  return error


def get_xml_version(version):
  """Determines which XML schema to use based on the client API version.
  
  Args:
    version: string which is converted to an int. The version string is in
             the form 'Major.Minor.x.y.z' and only the major version number
             is considered. If None is provided assume version 1. 
  """
  if version is None:
    return 1
  return int(version.split('.')[0])


class GDClient(atom.client.AtomPubClient):
  """Communicates with Google Data servers to perform CRUD operations.

  This class is currently experimental and may change in backwards
  incompatible ways.

  This class exists to simplify the following three areas involved in using
  the Google Data APIs.

  CRUD Operations:

  The client provides a generic 'request' method for making HTTP requests.
  There are a number of convenience methods which are built on top of
  request, which include get_feed, get_entry, get_next, post, update, and
  delete. These methods contact the Google Data servers.

  Auth:

  Reading user-specific private data requires authorization from the user as
  do any changes to user data. An auth_token object can be passed into any
  of the HTTP requests to set the Authorization header in the request.

  You may also want to set the auth_token member to a an object which can
  use modify_request to set the Authorization header in the HTTP request.

  If you are authenticating using the email address and password, you can
  use the client_login method to obtain an auth token and set the
  auth_token member.

  If you are using browser redirects, specifically AuthSub, you will want
  to use gdata.gauth.AuthSubToken.from_url to obtain the token after the
  redirect, and you will probably want to updgrade this since use token
  to a multiple use (session) token using the upgrade_token method.

  API Versions:

  This client is multi-version capable and can be used with Google Data API
  version 1 and version 2. The version should be specified by setting the
  api_version member to a string, either '1' or '2'. 
  """

  # The gsessionid is used by Google Calendar to prevent redirects.
  __gsessionid = None
  api_version = None
  # Name of the Google Data service when making a ClientLogin request.
  auth_service = None
  # URL prefixes which should be requested for AuthSub and OAuth.
  auth_scopes = None

  def request(self, method=None, uri=None, auth_token=None,
              http_request=None, converter=None, desired_class=None,
              redirects_remaining=4, **kwargs):
    """Make an HTTP request to the server.
    
    See also documentation for atom.client.AtomPubClient.request.

    If a 302 redirect is sent from the server to the client, this client
    assumes that the redirect is in the form used by the Google Calendar API.
    The same request URI and method will be used as in the original request,
    but a gsessionid URL parameter will be added to the request URI with
    the value provided in the server's 302 redirect response. If the 302
    redirect is not in the format specified by the Google Calendar API, a
    RedirectError will be raised containing the body of the server's
    response.

    The method calls the client's modify_request method to make any changes
    required by the client before the request is made. For example, a
    version 2 client could add a GData-Version: 2 header to the request in
    its modify_request method.

    Args:
      method: str The HTTP verb for this request, usually 'GET', 'POST', 
              'PUT', or 'DELETE'
      uri: atom.http_core.Uri, str, or unicode The URL being requested.
      auth_token: An object which sets the Authorization HTTP header in its
                  modify_request method. Recommended classes include 
                  gdata.gauth.ClientLoginToken and gdata.gauth.AuthSubToken
                  among others.
      http_request: (optional) atom.http_core.HttpRequest
      converter: function which takes the body of the response as it's only
                 argument and returns the desired object.
      desired_class: class descended from atom.core.XmlElement to which a
                     successful response should be converted. If there is no
                     converter function specified (converter=None) then the
                     desired_class will be used in calling the
                     atom.core.parse function. If neither
                     the desired_class nor the converter is specified, an
                     HTTP reponse object will be returned.
      redirects_remaining: (optional) int, if this number is 0 and the
                           server sends a 302 redirect, the request method
                           will raise an exception. This parameter is used in
                           recursive request calls to avoid an infinite loop.

    Any additional arguments are passed through to 
    atom.client.AtomPubClient.request.

    Returns:
      An HTTP response object (see atom.http_core.HttpResponse for a
      description of the object's interface) if no converter was
      specified and no desired_class was specified. If a converter function
      was provided, the results of calling the converter are returned. If no
      converter was specified but a desired_class was provided, the response
      body will be converted to the class using 
      atom.core.parse.
    """
    if isinstance(uri, (str, unicode)):
      uri = atom.http_core.Uri.parse_uri(uri)

    # Add the gsession ID to the URL to prevent further redirects.
    # TODO: If different sessions are using the same client, there will be a
    # multitude of redirects and session ID shuffling.
    # If the gsession ID is in the URL, adopt it as the standard location.
    if uri is not None and uri.query is not None and 'gsessionid' in uri.query:
      self.__gsessionid = uri.query['gsessionid']
    # The gsession ID could also be in the HTTP request.
    elif (http_request is not None and http_request.uri is not None
          and http_request.uri.query is not None
          and 'gsessionid' in http_request.uri.query):
      self.__gsessionid = http_request.uri.query['gsessionid']
    # If the gsession ID is stored in the client, and was not present in the
    # URI then add it to the URI.
    elif self.__gsessionid is not None:
      uri.query['gsessionid'] = self.__gsessionid

    # The AtomPubClient should call this class' modify_request before
    # performing the HTTP request.
    #http_request = self.modify_request(http_request)

    response = atom.client.AtomPubClient.request(self, method=method, 
        uri=uri, auth_token=auth_token, http_request=http_request, **kwargs)
    # On success, convert the response body using the desired converter 
    # function if present.
    if response is None:
      return None
    if response.status == 200 or response.status == 201:
      if converter is not None:
        return converter(response)
      elif desired_class is not None:
        if self.api_version is not None:
          return atom.core.parse(response.read(), desired_class,
                                 version=get_xml_version(self.api_version))
        else:
          # No API version was specified, so allow parse to
          # use the default version.
          return atom.core.parse(response.read(), desired_class)
      else:
        return response
    # TODO: move the redirect logic into the Google Calendar client once it
    # exists since the redirects are only used in the calendar API.
    elif response.status == 302:
      if redirects_remaining > 0:
        location = response.getheader('Location')
        if location is not None:
          m = re.compile('[\?\&]gsessionid=(\w*)').search(location)
          if m is not None:
            self.__gsessionid = m.group(1)
          # Make a recursive call with the gsession ID in the URI to follow 
          # the redirect.
          return self.request(method=method, uri=uri, auth_token=auth_token,
                              http_request=http_request, converter=converter,
                              desired_class=desired_class,
                              redirects_remaining=redirects_remaining-1,
                              **kwargs)
        else:
          raise error_from_response('302 received without Location header',
                                    response, RedirectError)
      else:
        raise error_from_response('Too many redirects from server', 
                                  response, RedirectError)
    elif response.status == 401:
      raise error_from_response('Unauthorized - Server responded with',
                                response, Unauthorized)
    # If the server's response was not a 200, 201, 302, or 401, raise an 
    # exception.
    else:
      raise error_from_response('Server responded with', response,
                                RequestError)

  Request = request

  def request_client_login_token(self, email, password, source, service=None,
      account_type='HOSTED_OR_GOOGLE', 
      auth_url=atom.http_core.Uri.parse_uri(
          'https://www.google.com/accounts/ClientLogin'), 
      captcha_token=None, captcha_response=None):
    service = service or self.auth_service
    # Set the target URL.
    http_request = atom.http_core.HttpRequest(uri=auth_url, method='POST')
    http_request.add_body_part(
        gdata.gauth.generate_client_login_request_body(email=email, 
            password=password, service=service, source=source, 
            account_type=account_type, captcha_token=captcha_token, 
            captcha_response=captcha_response),
        'application/x-www-form-urlencoded')

    # Use the underlying http_client to make the request.
    response = self.http_client.request(http_request)

    response_body = response.read()
    if response.status == 200:
      token_string = gdata.gauth.get_client_login_token_string(response_body)
      if token_string is not None:
        return gdata.gauth.ClientLoginToken(token_string)
      else:
        raise ClientLoginTokenMissing(
            'Recieved a 200 response to client login request,'
            ' but no token was present. %s' % (response_body,))
    elif response.status == 403:
      captcha_challenge = gdata.gauth.get_captcha_challenge(response_body)
      if captcha_challenge:
        challenge = CaptchaChallenge('CAPTCHA required')
        challenge.captcha_url = captcha_challenge['url']
        challenge.captcha_token = captcha_challenge['token']
        raise challenge
      elif response_body.splitlines()[0] == 'Error=BadAuthentication':
        raise BadAuthentication('Incorrect username or password')
      else:
        raise error_from_response('Server responded with a 403 code',
                                  response, RequestError, response_body)
    elif response.status == 302:
      # Google tries to redirect all bad URLs back to
      # http://www.google.<locale>. If a redirect
      # attempt is made, assume the user has supplied an incorrect
      # authentication URL
      raise error_from_response('Server responded with a redirect',
                                response, BadAuthenticationServiceURL,
                                response_body)
    else:
      raise error_from_response('Server responded to ClientLogin request',
                                response, ClientLoginFailed, response_body)

  RequestClientLoginToken = request_client_login_token

  def client_login(self, email, password, source, service=None,
                   account_type='HOSTED_OR_GOOGLE',
                   auth_url='https://www.google.com/accounts/ClientLogin',
                   captcha_token=None, captcha_response=None):
    service = service or self.auth_service
    self.auth_token = self.request_client_login_token(email, password,
        source, service=service, account_type=account_type, auth_url=auth_url,
        captcha_token=captcha_token, captcha_response=captcha_response)

  ClientLogin = client_login

  def upgrade_token(self, token=None, url=atom.http_core.Uri.parse_uri(
      'https://www.google.com/accounts/AuthSubSessionToken')):
    """Asks the Google auth server for a multi-use AuthSub token.

    For details on AuthSub, see:
    http://code.google.com/apis/accounts/docs/AuthSub.html
    
    Args:
      token: gdata.gauth.AuthSubToken or gdata.gauth.SecureAuthSubToken
          (optional) If no token is passed in, the client's auth_token member
          is used to request the new token. The token object will be modified
          to contain the new session token string.
      url: str or atom.http_core.Uri (optional) The URL to which the token
          upgrade request should be sent. Defaults to: 
          https://www.google.com/accounts/AuthSubSessionToken

    Returns:
      The upgraded gdata.gauth.AuthSubToken object.
    """
    # Default to using the auth_token member if no token is provided.
    if token is None:
      token = self.auth_token
    # We cannot upgrade a None token.
    if token is None:
      raise UnableToUpgradeToken('No token was provided.')
    if not isinstance(token, gdata.gauth.AuthSubToken):
      raise UnableToUpgradeToken(
          'Cannot upgrade the token because it is not an AuthSubToken object.')
    http_request = atom.http_core.HttpRequest(uri=url, method='GET')
    token.modify_request(http_request)
    # Use the lower level HttpClient to make the request.
    response = self.http_client.request(http_request)
    if response.status == 200:
      token._upgrade_token(response.read())
      return token
    else:
      raise UnableToUpgradeToken(
          'Server responded to token upgrade request with %s: %s' % (
              response.status, response.read()))

  UpgradeToken = upgrade_token

  def get_oauth_token(self, scopes, next, consumer_key, consumer_secret=None, 
                      rsa_private_key=None, 
                      url=gdata.gauth.REQUEST_TOKEN_URL):
    """Obtains an OAuth request token to allow the user to authorize this app.
    
    Once this client has a request token, the user can authorize the request
    token by visiting the authorization URL in their browser. After being
    redirected back to this app at the 'next' URL, this app can then exchange
    the authorized request token for an access token.

    For more information see the documentation on Google Accounts with OAuth:
    http://code.google.com/apis/accounts/docs/OAuth.html#AuthProcess

    Args:
      scopes: list of strings or atom.http_core.Uri objects which specify the
          URL prefixes which this app will be accessing. For example, to access
          the Google Calendar API, you would want to use scopes:
          ['https://www.google.com/calendar/feeds/',
           'http://www.google.com/calendar/feeds/']
      next: str or atom.http_core.Uri object, The URL which the user's browser
          should be sent to after they authorize access to their data. This
          should be a URL in your application which will read the token
          information from the URL and upgrade the request token to an access
          token.
      consumer_key: str This is the identifier for this application which you
          should have received when you registered your application with Google
          to use OAuth.
      consumer_secret: str (optional) The shared secret between your app and
          Google which provides evidence that this request is coming from you
          application and not another app. If present, this libraries assumes
          you want to use an HMAC signature to verify requests. Keep this data
          a secret.
      rsa_private_key: str (optional) The RSA private key which is used to 
          generate a digital signature which is checked by Google's server. If
          present, this library assumes that you want to use an RSA signature
          to verify requests. Keep this data a secret.
      url: The URL to which a request for a token should be made. The default
          is Google's OAuth request token provider.
    """
    http_request = None
    if rsa_private_key is not None:
      http_request = gdata.gauth.generate_request_for_request_token(
          consumer_key, gdata.gauth.RSA_SHA1, scopes,
          rsa_key=rsa_private_key, auth_server_url=url, next=next)
    elif consumer_secret is not None:
      http_request = gdata.gauth.generate_request_for_request_token(
          consumer_key, gdata.gauth.HMAC_SHA1, scopes,
          consumer_secret=consumer_secret, auth_server_url=url, next=next)
    else:
      raise MissingOAuthParameters(
          'To request an OAuth token, you must provide your consumer secret'
          ' or your private RSA key.')

    response = self.http_client.request(http_request)
    response_body = response.read()

    if response.status != 200:
      raise error_from_response('Unable to obtain OAuth request token',
                                response, RequestError, response_body)

    if rsa_private_key is not None:
      return gdata.gauth.rsa_token_from_body(response_body, consumer_key,
                                             rsa_private_key,
                                             gdata.gauth.REQUEST_TOKEN)
    elif consumer_secret is not None:
      return gdata.gauth.hmac_token_from_body(response_body, consumer_key,
                                              consumer_secret,
                                              gdata.gauth.REQUEST_TOKEN)

  GetOAuthToken = get_oauth_token

  def get_access_token(self, request_token, 
                       url=gdata.gauth.ACCESS_TOKEN_URL):
    """Exchanges an authorized OAuth request token for an access token.
    
    Contacts the Google OAuth server to upgrade a previously authorized
    request token. Once the request token is upgraded to an access token,
    the access token may be used to access the user's data.

    For more details, see the Google Accounts OAuth documentation:
    http://code.google.com/apis/accounts/docs/OAuth.html#AccessToken

    Args:
      request_token: An OAuth token which has been authorized by the user.
      url: (optional) The URL to which the upgrade request should be sent.
          Defaults to: https://www.google.com/accounts/OAuthAuthorizeToken
    """
    http_request = gdata.gauth.generate_request_for_access_token(
        request_token, auth_server_url=url)
    response = self.http_client.request(http_request)
    response_body = response.read()
    if response.status != 200:
      raise error_from_response(
          'Unable to upgrade OAuth request token to access token',
          response, RequestError, response_body)

    return gdata.gauth.upgrade_to_access_token(request_token, response_body)

  GetAccessToken = get_access_token

  def modify_request(self, http_request):
    """Adds or changes request before making the HTTP request.
    
    This client will add the API version if it is specified. 
    Subclasses may override this method to add their own request 
    modifications before the request is made.
    """
    http_request = atom.client.AtomPubClient.modify_request(self, 
                                                            http_request)
    if self.api_version is not None:
      http_request.headers['GData-Version'] = self.api_version
    return http_request

  ModifyRequest = modify_request

  def get_feed(self, uri, auth_token=None, converter=None, 
               desired_class=gdata.data.GDFeed, **kwargs):
    return self.request(method='GET', uri=uri, auth_token=auth_token,
                        converter=converter, desired_class=desired_class,
                        **kwargs)

  GetFeed = get_feed

  def get_entry(self, uri, auth_token=None, converter=None,
                desired_class=gdata.data.GDEntry, **kwargs):
    return self.request(method='GET', uri=uri, auth_token=auth_token,
                        converter=converter, desired_class=desired_class,
                        **kwargs)

  GetEntry = get_entry

  def get_next(self, feed, auth_token=None, converter=None, 
               desired_class=None, **kwargs):
    """Fetches the next set of results from the feed. 
    
    When requesting a feed, the number of entries returned is capped at a
    service specific default limit (often 25 entries). You can specify your
    own entry-count cap using the max-results URL query parameter. If there
    are more results than could fit under max-results, the feed will contain
    a next link. This method performs a GET against this next results URL.

    Returns:
      A new feed object containing the next set of entries in this feed.
    """
    if converter is None and desired_class is None:
      desired_class = feed.__class__
    return self.get_feed(feed.get_next_url(), auth_token=auth_token,
                         converter=converter, desired_class=desired_class,
                         **kwargs)

  GetNext = get_next

  # TODO: add a refresh method to re-fetch the entry/feed from the server
  # if it has been updated.

  def post(self, entry, uri, auth_token=None, converter=None, 
           desired_class=None, **kwargs):
    if converter is None and desired_class is None:
      desired_class = entry.__class__
    http_request = atom.http_core.HttpRequest()
    http_request.add_body_part(
        entry.to_string(get_xml_version(self.api_version)),
        'application/atom+xml')
    return self.request(method='POST', uri=uri, auth_token=auth_token,
                        http_request=http_request, converter=converter,
                        desired_class=desired_class, **kwargs)

  Post = post

  def update(self, entry, auth_token=None, force=False, **kwargs):
    """Edits the entry on the server by sending the XML for this entry.
    
    Performs a PUT and converts the response to a new entry object with a
    matching class to the entry passed in.

    Args:
      entry:
      auth_token:
      force: boolean stating whether an update should be forced. Defaults to
             False. Normally, if a change has been made since the passed in
             entry was obtained, the server will not overwrite the entry since
             the changes were based on an obsolete version of the entry.
             Setting force to True will cause the update to silently
             overwrite whatever version is present.

    Returns:
      A new Entry object of a matching type to the entry which was passed in.
    """
    http_request = atom.http_core.HttpRequest()
    http_request.add_body_part(
        entry.to_string(get_xml_version(self.api_version)),
        'application/atom+xml')
    # Include the ETag in the request if this is version 2 of the API.
    if self.api_version and self.api_version.startswith('2'):
      if force:
        http_request.headers['If-Match'] = '*'
      elif hasattr(entry, 'etag') and entry.etag:
        http_request.headers['If-Match'] = entry.etag
    return self.request(method='PUT', uri=entry.find_edit_link(), 
                        auth_token=auth_token, http_request=http_request, 
                        desired_class=entry.__class__, **kwargs)

  Update = update

  def delete(self, entry_or_uri, auth_token=None, force=False, **kwargs):
    # If the user passes in a URL, just delete directly, may not work as
    # the service might require an ETag.
    if isinstance(entry_or_uri, (str, unicode, atom.http_core.Uri)):
      return self.request(method='DELETE', uri=entry_or_uri,
                          auth_token=auth_token, **kwargs)
    http_request = atom.http_core.HttpRequest()
    # Include the ETag in the request if this is version 2 of the API.
    if self.api_version and self.api_version.startswith('2'):
      if force:
        http_request.headers['If-Match'] = '*'
      elif hasattr(entry_or_uri, 'etag') and entry_or_uri.etag:
        http_request.headers['If-Match'] = entry_or_uri.etag
    return self.request(method='DELETE', uri=entry_or_uri.find_edit_link(), 
                        http_request=http_request, auth_token=auth_token,
                        **kwargs)

  Delete = delete

  #TODO: implement batch requests.
  #def batch(feed, uri, auth_token=None, converter=None, **kwargs):
  #  pass

  # TODO: add a refresh method to request a conditional update to an entry
  # or feed.


def _add_query_param(param_string, value, http_request):
  if value:
    http_request.uri.query[param_string] = value


class Query(object):

  def __init__(self, text_query=None, categories=None, author=None, alt=None,
               updated_min=None, updated_max=None, pretty_print=False,
               published_min=None, published_max=None, start_index=None,
               max_results=None, strict=False):
    """Constructs a Google Data Query to filter feed contents serverside.
    
    Args:
      text_query: Full text search str (optional)
      categories: list of strings (optional). Each string is a required
          category. To include an 'or' query, put a | in the string between
          terms. For example, to find everything in the Fitz category and
          the Laurie or Jane category (Fitz and (Laurie or Jane)) you would
          set categories to ['Fitz', 'Laurie|Jane'].
      author: str (optional) The service returns entries where the author
          name and/or email address match your query string.
      alt: str (optional) for the Alternative representation type you'd like
          the feed in. If you don't specify an alt parameter, the service
          returns an Atom feed. This is equivalent to alt='atom'.
          alt='rss' returns an RSS 2.0 result feed.
          alt='json' returns a JSON representation of the feed.
          alt='json-in-script' Requests a response that wraps JSON in a script
          tag.
          alt='atom-in-script' Requests an Atom response that wraps an XML
          string in a script tag.
          alt='rss-in-script' Requests an RSS response that wraps an XML
          string in a script tag.
      updated_min: str (optional), RFC 3339 timestamp format, lower bounds. 
          For example: 2005-08-09T10:57:00-08:00
      updated_max: str (optional) updated time must be earlier than timestamp.
      pretty_print: boolean (optional) If True the server's XML response will
          be indented to make it more human readable. Defaults to False.
      published_min: str (optional), Similar to updated_min but for published
          time.
      published_max: str (optional), Similar to updated_max but for published
          time.
      start_index: int or str (optional) 1-based index of the first result to
          be retrieved. Note that this isn't a general cursoring mechanism. 
          If you first send a query with ?start-index=1&max-results=10 and
          then send another query with ?start-index=11&max-results=10, the
          service cannot guarantee that the results are equivalent to
          ?start-index=1&max-results=20, because insertions and deletions
          could have taken place in between the two queries.
      max_results: int or str (optional) Maximum number of results to be
          retrieved. Each service has a default max (usually 25) which can
          vary from service to service. There is also a service-specific
          limit to the max_results you can fetch in a request.
      strict: boolean (optional) If True, the server will return an error if
          the server does not recognize any of the parameters in the request
          URL. Defaults to False.
    """
    self.text_query = text_query
    self.categories = categories or []
    self.author = author
    self.alt = alt
    self.updated_min = updated_min
    self.updated_max = updated_max
    self.pretty_print = pretty_print
    self.published_min = published_min
    self.published_max = published_max
    self.start_index = start_index
    self.max_results = max_results
    self.strict = strict

  def modify_request(self, http_request):
    _add_query_param('q', self.text_query, http_request)
    if self.categories:
      http_request.uri.query['categories'] = ','.join(self.categories)
    _add_query_param('author', self.author, http_request)
    _add_query_param('alt', self.alt, http_request)
    _add_query_param('updated-min', self.updated_min, http_request)
    _add_query_param('updated-max', self.updated_max, http_request)
    if self.pretty_print:
      http_request.uri.query['prettyprint'] = 'true'
    _add_query_param('published-min', self.published_min, http_request)
    _add_query_param('published-max', self.published_max, http_request)
    if self.start_index is not None:
      http_request.uri.query['start-index'] = str(self.start_index)
    if self.max_results is not None:
      http_request.uri.query['max-results'] = str(self.max_results)
    if self.strict:
      http_request.uri.query['strict'] = 'true'


  ModifyRequest = modify_request


class GDQuery(atom.http_core.Uri):

  def _get_text_query(self):
    return self.query['q']

  def _set_text_query(self, value):
    self.query['q'] = value

  text_query = property(_get_text_query, _set_text_query, 
      doc='The q parameter for searching for an exact text match on content')
    



# Version 1 code.
SCOPE_URL_PARAM_NAME = gdata.service.SCOPE_URL_PARAM_NAME 
# Maps the service names used in ClientLogin to scope URLs. 
CLIENT_LOGIN_SCOPES = gdata.service.CLIENT_LOGIN_SCOPES


class AuthorizationRequired(gdata.service.Error):
  pass


class GDataClient(gdata.service.GDataService):
  """This class is deprecated. 
  
  All functionality has been migrated to gdata.service.GDataService.
  """
  @atom.deprecated('This class will be removed, use GDClient instead.')
  def __init__(self, application_name=None, tokens=None):
    gdata.service.GDataService.__init__(self, source=application_name, 
        tokens=tokens)

  @atom.deprecated('The GDataClient class will be removed in a future release'
                   ', use GDClient.ClientLogin instead')
  def ClientLogin(self, username, password, service_name, source=None, 
      account_type=None, auth_url=None, login_token=None, login_captcha=None):
    gdata.service.GDataService.ClientLogin(self, username=username, 
        password=password, account_type=account_type, service=service_name,
        auth_service_url=auth_url, source=source, captcha_token=login_token,
        captcha_response=login_captcha)

  @atom.deprecated('The GDataClient class will be removed in a future release'
                   ', use GDClient.GetEntry or GDClient.GetFeed')
  def Get(self, url, parser):
    """Simplified interface for Get.

    Requires a parser function which takes the server response's body as
    the only argument.

    Args:
      url: A string or something that can be converted to a string using str.
          The URL of the requested resource.
      parser: A function which takes the HTTP body from the server as it's
          only result. Common values would include str, 
          gdata.GDataEntryFromString, and gdata.GDataFeedFromString.

    Returns: The result of calling parser(http_response_body).
    """
    return gdata.service.GDataService.Get(self, uri=url, converter=parser)
  
  @atom.deprecated('The GDataClient class will be removed in a future release'
                   ', use GDClient.Post instead')
  def Post(self, data, url, parser, media_source=None):
    """Streamlined version of Post.

    Requires a parser function which takes the server response's body as
    the only argument.
    """
    return gdata.service.GDataService.Post(self, data=data, uri=url,
        media_source=media_source, converter=parser)

  @atom.deprecated('The GDataClient class will be removed in a future release'
                   ', use GDClient.Put instead')
  def Put(self, data, url, parser, media_source=None):
    """Streamlined version of Put.

    Requires a parser function which takes the server response's body as
    the only argument.
    """
    return gdata.service.GDataService.Put(self, data=data, uri=url,
        media_source=media_source, converter=parser)

  @atom.deprecated('The GDataClient class will be removed in a future release'
                   ', use GDClient.Delete instead')
  def Delete(self, url):
    return gdata.service.GDataService.Delete(self, uri=url)


ExtractToken = gdata.service.ExtractToken
GenerateAuthSubRequestUrl = gdata.service.GenerateAuthSubRequestUrl    
