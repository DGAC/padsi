# DBus router

Process which "routes" messages coming from DBus clients to one or more "remote" DBus services based on a set of rules.

This DBus router is meant to be an in between clients in a zone to be able to use only some specified services from
the host session DBus service, while using the zone's session DBus service for all the other interactions.

It was primarily created with a screen share use case in mind.
