project = "hiding-place-447317-c6"

deploy_user    = "berna"
deploy_ssh_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKwsI/C8ujm+xkzM1Yl3gPUiDOC/em15q6+oY/OY69Yq berna-gcp-2026-09-15"

# Twilio's signalling edges (twilio.com/docs/sip-trunking/ip-addresses), as infra/box/nftables.conf.
# On a box with other carriers or approved addresses, `ssh <box> sudo pinecall-runtime fence export
# > carrier_signalling.auto.tfvars.json` here gives the whole list, and Terraform reads it after this.
carrier_signalling = [
  "54.172.60.0/30",
  "54.244.51.0/30",
  "54.171.127.192/30",
  "35.156.191.128/30",
  "54.65.63.192/30",
  "54.169.127.128/30",
  "54.252.254.64/30",
  "177.71.206.192/30",
]
