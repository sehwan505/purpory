package credential

import (
	"testing"

	"github.com/zalando/go-keyring"
)

func TestKeyringLifecycle(t *testing.T) {
	keyring.MockInit()
	store := Keyring{}
	if err := store.Set("provider.openai.api-key", "secret"); err != nil {
		t.Fatal(err)
	}
	value, found, err := store.Get("provider.openai.api-key")
	if err != nil || !found || value != "secret" {
		t.Fatalf("credential = %q, %v, %v", value, found, err)
	}
	if err := store.Delete("provider.openai.api-key"); err != nil {
		t.Fatal(err)
	}
	if _, found, err := store.Get("provider.openai.api-key"); err != nil || found {
		t.Fatalf("deleted credential remains: %v, %v", found, err)
	}
}
