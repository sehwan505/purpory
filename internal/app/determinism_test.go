package app

import (
	"context"
	"path/filepath"
	"reflect"
	"testing"

	"github.com/sehwan505/purpory/internal/memory"
	"github.com/sehwan505/purpory/internal/prepare"
)

func TestModelFreeRetrievalRepeatsExactlyWithFixedSessionState(t *testing.T) {
	t.Setenv("PURPORY_SESSION", "anon")
	ctx := context.Background()
	root := t.TempDir()
	service := openTestService(t, root, filepath.Join(t.TempDir(), "purpory.db"), "demo")
	for _, key := range []string{"database.one", "database.two", "database.three"} {
		value := "Use the project database."
		if _, err := service.Remember(ctx, key, memory.Note, &value, nil); err != nil {
			t.Fatal(err)
		}
	}
	request := prepare.Request{Message: "database", SessionID: "anon", WorkingDirectory: root, TokenBudget: 512}
	want, err := service.PrepareContext(ctx, request)
	if err != nil || want.Action != "retrieve" {
		t.Fatalf("no deterministic hints: %#v %v", want, err)
	}
	wantQuery, err := service.Query(ctx, "database", 5)
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 20; i++ {
		got, err := service.PrepareContext(ctx, request)
		if err != nil || !reflect.DeepEqual(got, want) {
			t.Fatalf("prepare changed: %#v != %#v: %v", got, want, err)
		}
		query, err := service.Query(ctx, "database", 5)
		if err != nil || !reflect.DeepEqual(query, wantQuery) {
			t.Fatalf("query changed: %#v != %#v: %v", query, wantQuery, err)
		}
	}
}
