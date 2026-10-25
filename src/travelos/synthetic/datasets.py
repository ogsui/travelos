"""Constants and templates for the deterministic synthetic dataset.

Everything here is deliberately fake. Names use reserved example surnames,
emails use the reserved ``example.test`` domain and phone numbers use the
invalid ``+91 00000`` prefix, so a leaked fixture cannot contact or identify a
real person.
"""

from __future__ import annotations

from typing import Final

DEFAULT_SEED: Final = 20261025
GENERATOR_VERSION: Final = 1

CATEGORIES: Final[tuple[str, ...]] = (
    "domestic_flight",
    "international_flight",
    "family_package",
    "honeymoon",
    "pilgrimage",
    "corporate_multi_city",
    "group_mice",
    "visa_only",
    "hotel_only",
    "vague",
    "contradictory",
    "hinglish",
    "hindi",
    "date_change_midconversation",
    "misspelt_names",
    "long_layover",
)

FIRST_NAMES: Final[tuple[str, ...]] = (
    "Aarav",
    "Aditi",
    "Arjun",
    "Asha",
    "Diya",
    "Farhan",
    "Fatima",
    "Ishaan",
    "Kavya",
    "Karthik",
    "Meera",
    "Nikhil",
    "Priya",
    "Rahul",
    "Rohan",
    "Sana",
    "Sneha",
    "Vikram",
    "Zoya",
    "Dev",
    "Nisha",
    "Omkar",
    "Pooja",
    "Tanvi",
)
FAKE_SURNAMES: Final[tuple[str, ...]] = (
    "Synthetic",
    "Example",
    "Sample",
    "Fixture",
    "Demo",
    "Mock",
    "Placeholder",
    "Test",
)
CORPORATE_NAMES: Final[tuple[str, ...]] = (
    "Example Corp",
    "Sample Industries",
    "Fixture Labs",
    "Mock Travels",
    "Demo Systems",
)
SEGMENTS: Final[tuple[str, ...]] = (
    "new",
    "repeat",
    "family",
    "honeymoon",
    "corporate",
    "pilgrimage",
    "group",
    "inactive",
)

CHANNELS: Final[tuple[str, ...]] = (
    "whatsapp",
    "email",
    "sms",
    "phone",
    "instagram",
    "facebook",
    "google_business",
    "web_form",
)
CONSENT_SOURCES: Final[tuple[str, ...]] = (
    "web_form",
    "booking_form",
    "phone_call",
    "whatsapp_keyword",
    "import",
    "in_person",
)
MEAL_PREFERENCES: Final[tuple[str, ...]] = (
    "vegetarian",
    "jain",
    "no_preference",
    "halal",
    "vegan",
)
SEAT_PREFERENCES: Final[tuple[str, ...]] = ("window", "aisle", "no_preference")

ORIGIN_CITIES: Final[tuple[str, ...]] = (
    "Mumbai",
    "Delhi",
    "Bengaluru",
    "Chennai",
    "Kolkata",
    "Hyderabad",
    "Ahmedabad",
    "Pune",
)
DOMESTIC_DESTINATIONS: Final[tuple[str, ...]] = (
    "Goa",
    "Kerala",
    "Jaipur",
    "Udaipur",
    "Leh",
    "Andaman",
    "Darjeeling",
    "Coorg",
    "Varanasi",
    "Ayodhya",
    "Thiruvananthapuram",
)
INTERNATIONAL_DESTINATIONS: Final[tuple[str, ...]] = (
    "London",
    "Dubai",
    "Singapore",
    "Thailand",
    "Maldives",
    "New York",
    "Paris",
    "Tokyo",
)
HOTEL_NAMES: Final[tuple[str, ...]] = (
    "Example Beach Resort",
    "Sample Palm Hotel",
    "Fixture Heritage Stay",
    "Mock Lake View",
    "Demo Hill Retreat",
    "Placeholder Palace",
    "Test Backwater Villas",
    "Example City Suites",
)
ACTIVITY_NAMES: Final[tuple[str, ...]] = (
    "Example Sunset Cruise",
    "Sample Cooking Class",
    "Fixture Heritage Walk",
    "Mock Snorkelling Trip",
    "Demo Wildlife Safari",
    "Placeholder Temple Tour",
)
TRANSFER_NAMES: Final[tuple[str, ...]] = (
    "Example Airport Transfer",
    "Sample Private Car",
    "Fixture Coach Transfer",
    "Mock Railway Pickup",
)
VISA_COUNTRIES: Final[tuple[str, ...]] = (
    "United Kingdom",
    "Schengen",
    "United States",
    "Dubai",
    "Singapore",
)
