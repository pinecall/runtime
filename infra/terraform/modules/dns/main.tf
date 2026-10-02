# The names that point at the box. The zone holds other services' records too (the landing, the
# docs, the mail's DKIM): only these are Terraform's, the rest are their repositories'.
data "aws_route53_zone" "zone" {
  name = var.zone
}

resource "aws_route53_record" "box" {
  for_each = toset(var.names)
  zone_id  = data.aws_route53_zone.zone.zone_id
  name     = each.value
  type     = "A"
  ttl      = var.ttl
  records  = [var.address]
}
