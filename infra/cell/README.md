# The cell

The machines beside the box: a streaming replica of its Postgres, so the database outlives the
box (promoted by `pinecall-runtime box failover`, made the box by `box up`), and machines that run
gateways and nothing else, so the control plane grows past one machine, and machines that run one
world's workers and nothing else, so the calls grow past one machine. The procedures, and what was
measured: `docs/a-box-in-production.md`, "A replica, and failing over to it", "Gateways on other
machines" and "Workers on other machines".

```
primary.sh                  on the box: `allow <replica>` / `forget` (the role, the slot, pg_hba,
                            Postgres published toward the replica and fenced to it alone);
                            `allow-gateway <address>` / `forget-gateway <address>` (pg_hba for the
                            role pinecall, Postgres, Redis and LiveKit's API published on the box's
                            address and fenced to the cell's gateway machines, Caddy sending them
                            calls); `gateway-credentials` (the credentials a gateway machine runs
                            on, as a tar on stdout, for a pipe alone); `allow-worker <address>` /
                            `forget-worker <address>` (LiveKit's API published on the box's address,
                            it and the gateways' balancer fenced to the worker machines; no Postgres,
                            no Redis); `worker-settings <world>` (what a worker machine runs on
                            and is no secret: box.env, store.env, the fleet's env, for
                            `worker.sh image`); `worker-credentials <world>` (the same with the
                            fleet key, the LiveKit pair and the object store's secret, for a pipe
                            alone; refused with no recordings bucket)
replica.sh                  on the second machine: `join <box address>`, the replication password on stdin
gateway.sh                  on a gateway machine: `join <box address> <wheel> [processes]`, the
                            credentials on stdin; `release <wheel>`, the gateways one at a time
pinecall-gateway@.service   a gateway on a machine with no box: the box's unit less the migration and
                            the Postgres container, LiveKit at the box's address
gateway.nft                 a gateway machine's fence: ssh, and port 8090 from the box alone
worker.sh                   on a worker machine: `join <box address> <wheel> <world> [calls]`, the
                            credentials on stdin; `image …`, the same from `worker-settings`, no
                            credential on the disk, for the machine the fleet's image is frozen
                            from; `enroll`, a machine made from the image spending its join token
                            at its first boot; `release <wheel>`, the worker drained and restarted
pinecall-join.service       the first boot of a machine made from the image: `worker.sh enroll`,
                            skipped on a machine joined by hand (no /etc/pinecall/join.env)
pinecall-worker@.service    a worker on a machine with no box: the box's unit less the LiveKit,
                            gateway and fleet key it waits for, both at the box's address; it waits
                            for the enrolment
worker.nft                  a worker machine's fence: ssh, and nothing else in
pinecall-postgres.container the standby: the box's image, volume and container name
alerts.yaml                 the four alerts, as Prometheus rules over the gateway's /metrics
```

What a container is published on beyond loopback is kept in `/etc/pinecall/published/<container>.conf`,
a `PublishPort=` line each, and written into the installed container file by `primary.sh` and by
every `install.sh`: Ubuntu 24.04's podman (4.9) reads no `.container.d` drop-ins.

The second machine runs Ubuntu 24.04 in the box's own network and holds a copy of the box's
`/opt/pinecall/infra`, whose `box/` it also uses (the Postgres image, the users, the directories).
Nothing here is started on a box that has no replica: the fence's `replicas` set is empty and
Postgres is published on loopback alone.
