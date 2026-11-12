package ayasee.cloudflare

# Cloudflare edge, DNS and Zero Trust Access pack (issue #1). Provider v5 and v4 type names.
# The stage comes from `--data` (data.ayasee.stage), as in the Databricks pack; missing data
# means stage "" (not prod). Messages name the resource address only, never an attribute value.
#
# SG-CF-001: a proxied DNS record switched to DNS-only (deny in prod, warn elsewhere).
# SG-CF-002: an Access application removed (warn; the risk rule cf-access-destroy makes it critical).
# SG-CF-003: a WAF, firewall or rate-limiting rule removed (warn; cf-firewall-destroy is critical).
#   Removals are warnings, not denials: the workflows pass no flags, so a deny could never be
#   lifted and a legitimate removal could never reach prod. The prod reviewer sees them first.
# SG-CF-004: a tunnel ingress or route publishes a service it did not publish before (warn).
# SG-CF-005: a change to an MX record or to the zone apex "@" (warn).

default stage := ""

stage := data.ayasee.stage

dns_types := {"cloudflare_dns_record", "cloudflare_record"}

access_app_types := {"cloudflare_zero_trust_access_application", "cloudflare_access_application"}

firewall_types := {
	"cloudflare_ruleset",
	"cloudflare_firewall_rule",
	"cloudflare_filter",
	"cloudflare_access_rule",
	"cloudflare_rate_limit",
	"cloudflare_waf_rule",
	"cloudflare_waf_group",
	"cloudflare_waf_package",
	"cloudflare_waf_override",
}

tunnel_config_types := {"cloudflare_zero_trust_tunnel_cloudflared_config", "cloudflare_tunnel_config"}

tunnel_route_types := {"cloudflare_zero_trust_tunnel_cloudflared_route", "cloudflare_tunnel_route"}

removes(rc) if {
	some a in rc.change.actions
	a in {"delete", "forget"}
}

changes(rc) if {
	some a in rc.change.actions
	not a in {"no-op", "read"}
}

side(rc, name) := object.get(rc.change, name, null) if object.get(rc.change, name, null) != null

side(rc, name) := {} if object.get(rc.change, name, null) == null

# ---- SG-CF-001 ----

proxy_off_violations contains msg if {
	some rc in input.resource_changes
	rc.type in dns_types
	"update" in rc.change.actions
	side(rc, "before").proxied == true
	object.get(side(rc, "after"), "proxied", false) == false
	msg := sprintf("SG-CF-001: %s switches a proxied record to DNS-only, which publishes the origin address", [rc.address])
}

deny contains msg if {
	stage == "prod"
	some msg in proxy_off_violations
}

warn contains msg if {
	stage != "prod"
	some msg in proxy_off_violations
}

# ---- SG-CF-002 ----

warn contains msg if {
	some rc in input.resource_changes
	rc.type in access_app_types
	removes(rc)
	msg := sprintf("SG-CF-002: %s removes an Access application and the login in front of its origin", [rc.address])
}

# ---- SG-CF-003 ----

firewall_removal contains rc.address if {
	some rc in input.resource_changes
	rc.type in firewall_types
	removes(rc)
}

# a ruleset that keeps existing but loses rules
firewall_removal contains rc.address if {
	some rc in input.resource_changes
	rc.type == "cloudflare_ruleset"
	"update" in rc.change.actions
	count(object.get(side(rc, "after"), "rules", [])) < count(object.get(side(rc, "before"), "rules", []))
}

warn contains msg if {
	some address in firewall_removal
	msg := sprintf("SG-CF-003: %s removes a WAF or firewall rule", [address])
}

# ---- SG-CF-004 ----

ingress_services(values) := {s |
	some rule in object.get(object.get(values, "config", {}), "ingress", [])
	s := [object.get(rule, "hostname", ""), object.get(rule, "service", "")]
} if is_object(object.get(values, "config", {}))

# v4 writes config as a one-element list of blocks with ingress_rule blocks
ingress_services(values) := {s |
	some cfg in object.get(values, "config", {})
	is_object(cfg)
	some rule in object.get(cfg, "ingress_rule", [])
	s := [object.get(rule, "hostname", ""), object.get(rule, "service", "")]
} if is_array(object.get(values, "config", {}))

warn contains msg if {
	some rc in input.resource_changes
	rc.type in tunnel_config_types
	changes(rc)
	count(ingress_services(side(rc, "after")) - ingress_services(side(rc, "before"))) > 0
	msg := sprintf("SG-CF-004: %s publishes a tunnel ingress (hostname and service) it did not publish before", [rc.address])
}

warn contains msg if {
	some rc in input.resource_changes
	rc.type in tunnel_route_types
	some a in rc.change.actions
	a in {"create", "update"}
	msg := sprintf("SG-CF-004: %s routes a network through a tunnel", [rc.address])
}

# ---- SG-CF-005 ----

sensitive_record(values) if values.type == "MX"

sensitive_record(values) if values.name == "@"

warn contains msg if {
	some rc in input.resource_changes
	rc.type in dns_types
	changes(rc)
	some name in ["before", "after"]
	sensitive_record(side(rc, name))
	msg := sprintf("SG-CF-005: %s changes an MX record or the zone apex", [rc.address])
}
