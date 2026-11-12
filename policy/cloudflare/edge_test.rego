package cloudflare_pack_test

import data.ayasee.cloudflare

plan(type, actions, before, after) := {"resource_changes": [{
	"address": sprintf("%s.x", [type]),
	"type": type,
	"change": {"actions": actions, "before": before, "after": after},
}]}

# ---- SG-CF-001 ----

proxy_off(type) := plan(type, ["update"], {"name": "www", "type": "A", "proxied": true}, {"name": "www", "type": "A", "proxied": false})

test_001_denies_proxy_off_in_prod if {
	msgs := cloudflare.deny with input as proxy_off("cloudflare_dns_record") with data.ayasee.stage as "prod"
	msgs == {"SG-CF-001: cloudflare_dns_record.x switches a proxied record to DNS-only, which publishes the origin address"}
}

test_001_covers_v4_records if {
	count(cloudflare.deny) == 1 with input as proxy_off("cloudflare_record") with data.ayasee.stage as "prod"
}

test_001_warns_outside_prod if {
	count(cloudflare.deny) == 0 with input as proxy_off("cloudflare_dns_record") with data.ayasee.stage as "staging"
	count(cloudflare.warn) == 1 with input as proxy_off("cloudflare_dns_record") with data.ayasee.stage as "staging"
	count(cloudflare.warn) == 1 with input as proxy_off("cloudflare_dns_record")
}

test_001_ignores_proxy_on_and_unproxied_records if {
	on := plan("cloudflare_dns_record", ["update"], {"name": "www", "type": "A", "proxied": false}, {"name": "www", "type": "A", "proxied": true})
	count(cloudflare.deny) == 0 with input as on with data.ayasee.stage as "prod"
	count(cloudflare.warn) == 0 with input as on with data.ayasee.stage as "prod"
	ttl := plan("cloudflare_dns_record", ["update"], {"name": "www", "type": "A", "proxied": true, "ttl": 1}, {"name": "www", "type": "A", "proxied": true, "ttl": 60})
	count(cloudflare.deny) == 0 with input as ttl with data.ayasee.stage as "prod"
}

# ---- SG-CF-002 ----

test_002_warns_on_access_application_removal if {
	p := plan("cloudflare_zero_trust_access_application", ["delete"], {"domain": "a"}, null)
	msgs := cloudflare.warn with input as p with data.ayasee.stage as "prod"
	msgs == {"SG-CF-002: cloudflare_zero_trust_access_application.x removes an Access application and the login in front of its origin"}
	count(cloudflare.deny) == 0 with input as p with data.ayasee.stage as "prod"
	count(cloudflare.warn) == 1 with input as plan("cloudflare_access_application", ["delete", "create"], {"domain": "a"}, {"domain": "a"})
	count(cloudflare.warn) == 1 with input as plan("cloudflare_access_application", ["forget"], {"domain": "a"}, null)
}

test_002_ignores_application_update if {
	count(cloudflare.warn) == 0 with input as plan("cloudflare_zero_trust_access_application", ["update"], {"domain": "a"}, {"domain": "b"})
}

# ---- SG-CF-003 ----

test_003_warns_on_firewall_rule_deletion if {
	p := plan("cloudflare_firewall_rule", ["delete"], {"action": "block"}, null)
	msgs := cloudflare.warn with input as p with data.ayasee.stage as "prod"
	msgs == {"SG-CF-003: cloudflare_firewall_rule.x removes a WAF or firewall rule"}
	count(cloudflare.deny) == 0 with input as p with data.ayasee.stage as "prod"
	count(cloudflare.warn) == 1 with input as plan("cloudflare_ruleset", ["delete"], {"rules": [{}]}, null)
}

test_003_warns_on_ruleset_losing_rules if {
	p := plan("cloudflare_ruleset", ["update"], {"rules": [{"ref": "a"}, {"ref": "b"}]}, {"rules": [{"ref": "a"}]})
	count(cloudflare.warn) == 1 with input as p
}

test_003_ignores_ruleset_growing_or_editing if {
	more := plan("cloudflare_ruleset", ["update"], {"rules": [{"ref": "a"}]}, {"rules": [{"ref": "a"}, {"ref": "b"}]})
	count(cloudflare.warn) == 0 with input as more
	edit := plan("cloudflare_ruleset", ["update"], {"rules": [{"ref": "a", "action": "block"}]}, {"rules": [{"ref": "a", "action": "challenge"}]})
	count(cloudflare.warn) == 0 with input as edit
	count(cloudflare.warn) == 0 with input as plan("cloudflare_ruleset", ["create"], null, {"rules": [{}]})
}

# ---- SG-CF-004 ----

v5_config(ingress) := {"config": {"ingress": ingress}}

test_004_warns_on_a_new_v5_ingress if {
	before := v5_config([{"hostname": "a.example.com", "service": "http://localhost:80"}, {"service": "http_status:404"}])
	after := v5_config([
		{"hostname": "a.example.com", "service": "http://localhost:80"},
		{"hostname": "b.example.com", "service": "ssh://localhost:22"},
		{"service": "http_status:404"},
	])
	msgs := cloudflare.warn with input as plan("cloudflare_zero_trust_tunnel_cloudflared_config", ["update"], before, after)
	msgs == {"SG-CF-004: cloudflare_zero_trust_tunnel_cloudflared_config.x publishes a tunnel ingress (hostname and service) it did not publish before"}
	count(cloudflare.deny) == 0 with input as plan("cloudflare_zero_trust_tunnel_cloudflared_config", ["update"], before, after)
}

test_004_warns_on_a_changed_port if {
	before := v5_config([{"hostname": "a.example.com", "service": "http://localhost:80"}])
	after := v5_config([{"hostname": "a.example.com", "service": "http://localhost:8080"}])
	count(cloudflare.warn) == 1 with input as plan("cloudflare_zero_trust_tunnel_cloudflared_config", ["update"], before, after)
}

test_004_no_warning_when_an_ingress_is_removed if {
	before := v5_config([{"hostname": "a.example.com", "service": "http://localhost:80"}, {"service": "http_status:404"}])
	after := v5_config([{"service": "http_status:404"}])
	count(cloudflare.warn) == 0 with input as plan("cloudflare_zero_trust_tunnel_cloudflared_config", ["update"], before, after)
}

test_004_reads_v4_ingress_rule_blocks if {
	after := {"config": [{"ingress_rule": [{"hostname": "a.example.com", "service": "http://localhost:80"}]}]}
	count(cloudflare.warn) == 1 with input as plan("cloudflare_tunnel_config", ["create"], null, after)
}

test_004_warns_on_a_tunnel_route if {
	count(cloudflare.warn) == 1 with input as plan("cloudflare_zero_trust_tunnel_cloudflared_route", ["create"], null, {"network": "10.0.0.0/8"})
	count(cloudflare.warn) == 0 with input as plan("cloudflare_zero_trust_tunnel_cloudflared_route", ["delete"], {"network": "10.0.0.0/8"}, null)
}

# ---- SG-CF-005 ----

test_005_warns_on_mx_and_apex if {
	mx := plan("cloudflare_dns_record", ["update"], {"name": "example.com", "type": "MX"}, {"name": "example.com", "type": "MX", "priority": 20})
	msgs := cloudflare.warn with input as mx
	msgs == {"SG-CF-005: cloudflare_dns_record.x changes an MX record or the zone apex"}
	count(cloudflare.deny) == 0 with input as mx with data.ayasee.stage as "prod"
	count(cloudflare.warn) == 1 with input as plan("cloudflare_record", ["delete"], {"name": "@", "type": "A"}, null)
	count(cloudflare.warn) == 1 with input as plan("cloudflare_dns_record", ["create"], null, {"name": "@", "type": "AAAA"})
}

test_005_ignores_other_records if {
	count(cloudflare.warn) == 0 with input as plan("cloudflare_dns_record", ["create"], null, {"name": "www", "type": "CNAME"})
}

test_pack_ignores_unrelated_resources if {
	p := plan("azurerm_databricks_workspace", ["delete"], {"name": "w"}, null)
	count(cloudflare.deny) == 0 with input as p with data.ayasee.stage as "prod"
	count(cloudflare.warn) == 0 with input as p
}
