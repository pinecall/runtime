# Packer: the fleet's worker image

`worker.pkr.hcl` builds the image a managed instance group makes the fleet's machines from: a
temporary e2-standard-2 made a worker of the world by `pinecall-runtime cell image-worker` (the
box's settings, no credential), frozen into the family `pinecall-worker-<world>`, the machine
deleted. The last step fails the build if anything is in the credential store.

```console
$ make image WORLD=production      # the wheel of this checkout, the box's settings, packer build
```

`make image` builds the wheel, fetches `cell worker-settings <world>` from the box into a file
(no secret is in it), and runs `packer build` with the gcloud login's token in the environment.
The instance template (`infra/terraform/modules/fleet-gcp`) reads the family's newest image: a
new image is taken by each machine the group makes from then on; the ones running keep theirs
until the fleet loop lets them go.

The seats are the machine type's, not the build machine's: `--calls` is given as `SEATS` (32 for
the e2-standard-8 the fleet runs as).

On AWS the same file builds an AMI named `pinecall-worker-<world>-<time>` (`-only=amazon-ebs.worker`,
the aws CLI's credentials, Canonical's Ubuntu 24.04 on a `c7a.large`), with the aws CLI installed
for `cell enroll` to read Secrets Manager; `modules/fleet-aws` boots the newest. No box runs on AWS
yet, so no AMI has been built: `packer validate` is what stands for it.
