# Web proxy and redirection

Web server which, depending on the requested features:
- proxies some Web requests to a third party proxy (and serves a wpad.dat file)
- performs Web redirection which is triggered when the user tries to reach a Web site which is blocked by the zone's configuration:
    - shows a "blocked" result page (uing an ad-hoc CA to generate certificates if necessary)
    - request that a notification be sent to the notification service for the user's session
