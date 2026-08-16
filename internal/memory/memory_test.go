package memory

import "testing"

func TestNew(t *testing.T) {
	value := "  Use SQLite  "
	got, err := New("project", "decision.database", Decision, &value, nil)
	if err != nil {
		t.Fatal(err)
	}
	if *got.Value != "Use SQLite" || got.Hash == "" {
		t.Fatalf("unexpected memory: %#v", got)
	}
	if _, err := New("project", "bad key", Note, &value, nil); err == nil {
		t.Fatal("invalid key accepted")
	}
	if _, err := New("project", "valid.key", Note, &value, &value); err == nil {
		t.Fatal("value and source accepted together")
	}
}
