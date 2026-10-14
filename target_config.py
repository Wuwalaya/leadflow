"""Explicit supported markets/products; no generic country or material fallback."""
from dataclasses import dataclass
import re
import unicodedata


def normalized(value: str) -> str:
    value = unicodedata.normalize('NFKD', value.lower())
    return ' '.join(re.sub(r'[^a-z0-9 ]', ' ', ''.join(
        char for char in value if not unicodedata.combining(char))).split())


def contains(text: str, term: str) -> bool:
    return f' {normalized(term)} ' in f' {normalized(text)} '


@dataclass(frozen=True)
class CountryConfig:
    name: str
    aliases: tuple[str, ...]
    tavily_country: str
    evidence_terms: tuple[str, ...]
    roles: tuple[tuple[str, tuple[str, ...]], ...]
    contact_terms: str


@dataclass(frozen=True)
class ProductConfig:
    name: str
    aliases: tuple[str, ...]
    material: str
    material_terms: tuple[tuple[str, tuple[str, ...]], ...]
    forms: tuple[tuple[str, tuple[str, ...]], ...]
    form_description: str


ENGLISH_ROLES = (
    ('distributor', ('distributor', 'distribution', 'stockist')),
    ('service_center', ('service center', 'service centre')),
    ('fabricator', ('fabricator', 'fabrication', 'manufacturer')),
)
COUNTRIES = (
    CountryConfig('Brazil', ('Brazil', 'Brasil', 'BR'), 'brazil',
                  ('Brazil', 'Brasil', 'São Paulo', 'Blumenau', 'Guarulhos',
                   'Santa Catarina', 'Curitiba', 'Porto Alegre', 'Rio de Janeiro',
                   'Belo Horizonte', 'Joinville'),
                  (('distributor', ('distribuidora', 'distribuidor', 'distribuicao', 'distribuimos', 'amplo estoque')),
                   ('service_center', ('centro de servicos',)),
                   ('fabricator', ('fabricamos', 'fabricacao'))) + ENGLISH_ROLES,
                  'contato email'),
    # Deliberately avoid bare "us" and state abbreviations: "contact us" is not location evidence.
    CountryConfig('United States', ('United States', 'United States of America', 'USA', 'US', 'U.S.', 'U.S.A.'),
                  'united states', ('United States', 'USA', 'U.S.A.'), ENGLISH_ROLES, 'contact email'),
)
PRODUCTS = (
    ProductConfig('Stainless Steel Flat/Profiles', ('Stainless Steel', 'inox', '不锈钢'),
                  'Stainless Steel',
                  (('Brazil', ('inox', 'aço inox', 'aço inoxidável', 'stainless steel')),
                   ('United States', ('stainless steel',))),
                  (('sheet_plate', ('sheet', 'sheets', 'plate', 'plates')),
                   ('coil_strip', ('coil', 'coils', 'strip', 'strips')),
                   ('flat_bar', ('flat bar', 'flat bars')),
                   ('angle_channel', ('angle', 'angles', 'channel', 'channels')),
                   ('profiles_other', ('stainless profile', 'stainless profiles'))), 'Flat/Profiles'),
    ProductConfig('Carbon Steel Sheet/Plate', ('Carbon Steel Sheet Plate',), 'Carbon Steel',
                  (('United States', ('carbon steel', 'mild steel')),),
                  (('sheet_plate', ('sheet', 'sheets', 'plate', 'plates')),), 'Sheet/Plate'),
)
PORTUGUESE_FORMS = {
    'sheet_plate': ('chapa', 'chapas'),
    'coil_strip': ('bobina', 'bobinas', 'fitas', 'tiras'),
    'flat_bar': ('barra chata', 'barras chatas'),
    'angle_channel': ('cantoneira', 'cantoneiras', 'canal u', 'perfil u'),
    'profiles_other': ('perfil inox', 'perfis inox'),
}
BRAZIL_STAINLESS_FLAT_QUERIES = (
    'Brasil distribuidora "aço inox" chapas bobinas',
    'Brasil "aço inoxidável" "centro de serviços"',
    'Brasil importadora "aço inox" chapas',
    'Brasil distribuidor inox fitas bobinas contato',
)
BRAZIL_STAINLESS_PROFILE_QUERIES = (
    'Brasil distribuidora "barras chatas" inox',
    'Brasil "cantoneiras" "aço inox" distribuidora',
    'Brasil "perfil U" "inox" distribuidor',
    'Brasil importadora "perfis" "aço inoxidável"',
)
BRAZIL_STAINLESS_DISCOVERY_QUERIES = (
    'Brasil distribuidor "aço inox" empresa',
    'Brasil distribuidora "aço inoxidável" empresa',
    'Brasil "centro de serviços" "aço inox"',
    'Brasil "chapas de aço inox" distribuidor',
    'Brasil "bobinas de aço inox" distribuidor',
    'Brasil "barras chatas" inox fornecedor',
    'Brasil cantoneiras inox distribuidor',
    'Brasil perfis inox fornecedor',
    'Brazil "stainless steel distributor"',
    'Brazil "stainless steel service center"',
    'Brazil "stainless steel stockist"',
    'Brasil importadora "aço inoxidável"',
)


@dataclass(frozen=True)
class TargetConfig:
    country: CountryConfig
    product: ProductConfig
    scope: str = 'both'

    @property
    def material_terms(self) -> tuple[str, ...]:
        return dict(self.product.material_terms)[self.country.name]

    @property
    def forms(self) -> dict[str, tuple[str, ...]]:
        forms = dict(self.product.forms)
        if self.scope != 'both':
            forms = {key: terms for key, terms in forms.items()
                     if (key in {'sheet_plate', 'coil_strip'}) == (self.scope == 'flat')}
        if self.country.name == 'Brazil':
            forms = {key: terms + PORTUGUESE_FORMS.get(key, ()) for key, terms in forms.items()}
        return forms

    @property
    def is_stainless(self) -> bool:
        return self.product.name == 'Stainless Steel Flat/Profiles'

    @property
    def material_question(self) -> str:
        # Preserve the existing Brazil/stainless typed-answer contract.
        return 'stainless' if self.is_stainless else 'material'

    @property
    def status(self) -> str:
        return 'historical_live_baseline' if self.country.name == 'Brazil' else 'experimental_offline_validated'

    def as_dict(self) -> dict:
        return {'country': self.country.name, 'product': self.product.name,
                'material': self.product.material, 'forms': self.product.form_description,
                'scope': self.scope, 'allowed_forms': list(self.forms), 'support_status': self.status}

    def has_material(self, text: str) -> bool:
        return any(contains(text, term) for term in self.material_terms)

    def extraction_query(self) -> str:
        terms = ' '.join(term for words in self.forms.values() for term in words)
        return f'{self.country.name} company {self.product.material} {terms} distributor stockist service center location address'

    def supplemental_query(self, domain: str) -> str:
        terms = ' OR '.join(f'"{term}"' for words in self.forms.values() for term in words)
        material = ' OR '.join(f'"{term}"' for term in self.material_terms)
        return f'site:{domain} ({material}) ({terms})'

    def search_queries(self, discovery: bool = False) -> list[str]:
        if self.country.name == 'Brazil' and self.is_stainless:
            if discovery and self.scope == 'both':
                return list(BRAZIL_STAINLESS_DISCOVERY_QUERIES)
            flat, profiles = BRAZIL_STAINLESS_FLAT_QUERIES, BRAZIL_STAINLESS_PROFILE_QUERIES
            return list(flat if self.scope == 'flat' else profiles if self.scope == 'profiles' else flat + profiles)
        forms = ' '.join(words[0] for words in self.forms.values())
        return [f'{self.country.name} "{self.product.material}" {forms} {role} company contact'
                for role in ('distributor', 'importer', 'stockist', 'service center', 'industrial manufacturer', 'wholesale')]


def resolve_target(country: str = 'Brazil', product: str = 'Stainless Steel Flat/Profiles', scope: str = 'both') -> TargetConfig:
    country_config = next((item for item in COUNTRIES if normalized(country) in
                           {normalized(alias) for alias in item.aliases}), None)
    product_config = next((item for item in PRODUCTS if product.casefold() in
                           {alias.casefold() for alias in item.aliases} or normalized(product) == normalized(item.name)), None)
    if country_config is None or product_config is None:
        raise ValueError(f'Unsupported country/product configuration: {country!r} + {product!r}')
    if country_config.name not in dict(product_config.material_terms):
        raise ValueError(f'Unsupported combination: {country_config.name} + {product_config.name}')
    if scope not in {'both', 'flat', 'profiles'}:
        raise ValueError(f'Unsupported scope: {scope}')
    target = TargetConfig(country_config, product_config, scope)
    if not target.forms:
        raise ValueError(f'Product {product_config.name} has no forms for scope={scope}')
    return target
