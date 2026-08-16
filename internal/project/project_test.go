package project

import (
	"testing"
)

func TestIdentify(t *testing.T) {
	t.Setenv("PURPORY_PROJECT_ID", "from-env")
	current := Project{ID: "observed"}
	if got := Identify(current, " explicit "); got.ID != "explicit" {
		t.Fatalf("explicit ID not used: %#v", got)
	}
	if got := Identify(current, ""); got.ID != "from-env" {
		t.Fatalf("environment ID not used: %#v", got)
	}
}
