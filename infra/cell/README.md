# The cell

What a second machine needs to hold a streaming replica of the box's Postgres, so the database
outlives the box: promoted by `pinecall-runtime box failover`, and made the box by `box up`. The
procedure, the RTO and the drill: `docs/a-box-in-production.md`, "A replica, and failing over to it".

```
primary.sh                  on the box: `allow <replica address>` (the role, the slot, pg_hba, Postgres
                            published toward the replica and fenced to it alone), `forget`
replica.sh                  on the second machine: `join <box address>`, the replication password on stdin
pinecall-postgres.container the standby: the box's image, volume and container name
alerts.yaml                 the four alerts, as Prometheus rules over the gateway's /metrics
```

The second machine runs Ubuntu 24.04 in the box's own network and holds a copy of the box's
`/opt/pinecall/infra`, whose `box/` it also uses (the Postgres image, the users, the directories).
Nothing here is started on a box that has no replica: the fence's `replicas` set is empty and
Postgres is published on loopback alone.
