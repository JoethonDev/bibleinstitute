"""Shared normalization for user-facing search fields."""

import unicodedata

from django.db.models import CharField, Func, Q, TextField
from django.db.models.lookups import Lookup


# These are common Arabic spelling variants that are safe to treat as
# equivalent for search.  We intentionally do not fold ta marbuta (ة) into
# ha (ه), because that would turn distinct names and words into false matches.
ARABIC_SEARCH_TRANSLATION = str.maketrans({
    "أ": "ا",
    "إ": "ا",
    "آ": "ا",
    "ٱ": "ا",
    "ى": "ي",
    "ئ": "ي",
    "ؤ": "و",
})

ARABIC_SEARCH_REMOVED_CHARS = "ًٌٍَُِّْـٰ"
ARABIC_SEARCH_REMOVED_TRANSLATION = str.maketrans(
    "",
    "",
    ARABIC_SEARCH_REMOVED_CHARS,
)


def normalize_search_text(value: object) -> str:
    """Return the canonical form used by application/user search."""

    normalized = unicodedata.normalize("NFKC", str(value or ""))
    normalized = normalized.translate(ARABIC_SEARCH_TRANSLATION)
    normalized = normalized.translate(ARABIC_SEARCH_REMOVED_TRANSLATION)
    normalized = normalized.casefold()
    return " ".join(normalized.split())


class SearchNormalize(Func):
    """Database equivalent of :func:`normalize_search_text`.

    The function is installed by a PostgreSQL migration and is immutable so
    PostgreSQL can use the matching functional trigram indexes.
    """

    function = "lms_arabic_search_normalize"
    arity = 1
    output_field = CharField()


class NormalizedContains(Lookup):
    """A Django lookup backed by the PostgreSQL search function."""

    lookup_name = "normalized_contains"
    prepare_rhs = False

    def as_sql(self, compiler, connection):
        lhs_sql, lhs_params = self.process_lhs(compiler, connection)
        normalized_value = normalize_search_text(self.rhs)
        if not normalized_value:
            return "1 = 0", []
        if connection.vendor != "postgresql":
            # PostgreSQL is authoritative and owns the indexed function. Keep
            # non-PostgreSQL tooling/tests executable without pretending that
            # it has the same stored-value equivalence semantics.
            return f"{lhs_sql} LIKE %s", [*lhs_params, f"%{normalized_value}%"]
        return (
            f"lms_arabic_search_normalize({lhs_sql}) LIKE %s",
            [*lhs_params, f"%{normalized_value}%"],
        )


CharField.register_lookup(NormalizedContains)
TextField.register_lookup(NormalizedContains)


def normalized_contains_q(fields: tuple[str, ...], value: object) -> Q:
    """Build an OR query using the canonical normalization for each field."""

    normalized_value = normalize_search_text(value)
    if not normalized_value:
        return Q()
    query = Q()
    for field in fields:
        query |= Q(**{f"{field}__normalized_contains": normalized_value})
    return query
