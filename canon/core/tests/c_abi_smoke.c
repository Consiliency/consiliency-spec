/* C-ABI smoke test for the canon-core `c-binding` feature (DABI / IF-0-DABI-1).
 *
 * Proves the extern "C" surface is byte-identical to the engine by round-tripping a corpus vector
 * (object-keys-sorted) through the C ABI and asserting the canonical bytes + digest match the pinned
 * corpus, then asserting a reject vector (lone surrogate) surfaces CANON_ERR (never a silent pass).
 *
 * Build + run: canon/core/scripts/check_cabi_smoke.sh
 */
#include "canon_core.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* object-keys-sorted: reordered keys canonicalize to sorted {"a":1,"b":2,"c":3}. */
static const char *INPUT =
    "{\"$obj\":{\"b\":{\"$int\":\"2\"},\"a\":{\"$int\":\"1\"},\"c\":{\"$int\":\"3\"}}}";
static const char *EXPECTED_BYTES = "{\"a\":1,\"b\":2,\"c\":3}";
static const char *EXPECTED_DIGEST =
    "b549fc7bcd2e283ef680d5ad4f2ff17841d3259fae8506661b83be5e4c64efc0";
static const char *PROFILE = "semantic-content";

/* reject-lone-surrogate: a lone high surrogate must be rejected end-to-end (CANON_ERR). */
static const char *REJECT_INPUT = "{\"$str\":\"\\ud800\"}";

static int failures = 0;

static void check(int cond, const char *label) {
    if (cond) {
        printf("ok   - %s\n", label);
    } else {
        printf("FAIL - %s\n", label);
        failures += 1;
    }
}

int main(void) {
    /* 1. canonical bytes round-trip */
    uint8_t *bytes = NULL;
    uintptr_t len = 0;
    char *err = NULL;
    int rc = canon_canonical_bytes_from_json(INPUT, &bytes, &len, &err);
    check(rc == CANON_OK, "canonical_bytes returns CANON_OK");
    check(err == NULL, "canonical_bytes leaves err NULL on success");
    check(len == strlen(EXPECTED_BYTES), "canonical_bytes length matches corpus");
    check(bytes != NULL && memcmp(bytes, EXPECTED_BYTES, len) == 0,
          "canonical_bytes are byte-identical to the corpus");
    canon_bytes_free(bytes, len);

    /* 2. digest round-trip */
    char *digest = NULL;
    err = NULL;
    rc = canon_digest_from_json(INPUT, PROFILE, &digest, &err);
    check(rc == CANON_OK, "digest returns CANON_OK");
    check(digest != NULL && strcmp(digest, EXPECTED_DIGEST) == 0,
          "digest is identical to the corpus");
    canon_string_free(digest);

    /* 3. reject vector: lone surrogate must fail loud (CANON_ERR + err message, no bytes) */
    bytes = NULL;
    len = 0;
    err = NULL;
    rc = canon_canonical_bytes_from_json(REJECT_INPUT, &bytes, &len, &err);
    check(rc == CANON_ERR, "lone surrogate returns CANON_ERR (fail-loud)");
    check(bytes == NULL && len == 0, "reject leaves no output bytes");
    check(err != NULL, "reject populates an error message");
    if (err != NULL) {
        printf("     (reject message: %s)\n", err);
    }
    canon_string_free(err);

    /* 4. null-input safety: never crash, always CANON_ERR */
    bytes = NULL;
    err = NULL;
    rc = canon_canonical_bytes_from_json(NULL, &bytes, &len, &err);
    check(rc == CANON_ERR, "null input returns CANON_ERR (no crash)");
    canon_string_free(err);

    if (failures == 0) {
        printf("\nC-ABI smoke: ALL CHECKS PASSED\n");
        return 0;
    }
    printf("\nC-ABI smoke: %d CHECK(S) FAILED\n", failures);
    return 1;
}
