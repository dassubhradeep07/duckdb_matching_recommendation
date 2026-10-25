import csv
import random
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_DIR / "data"
ROW_COUNT = 10_000
GROUP_SIZE = 10
FIELDS = (
    "customer_id",
    "customer_name",
    "email",
    "phone",
    "address",
    "city",
    "state",
    "postal_code",
    "country",
)
FIRST_NAMES = (
    "Avery", "Blake", "Casey", "Dakota", "Emerson", "Finley", "Gray", "Harper",
    "Indigo", "Jordan", "Kai", "Logan", "Morgan", "Nico", "Oakley", "Parker",
    "Quinn", "Reese", "Sage", "Taylor",
)
LAST_NAMES = (
    "Adams", "Bennett", "Carter", "Diaz", "Ellis", "Foster", "Garcia", "Hayes",
    "Ibrahim", "Jensen", "Kim", "Lopez", "Miller", "Nguyen", "Owens", "Patel",
    "Quinn", "Reed", "Singh", "Turner",
)
STREETS = (
    "Oak Street", "Pine Avenue", "Cedar Road", "Lake Drive", "Hill Lane",
    "Maple Court", "River Street", "Sunset Boulevard",
)
CITIES = (
    ("Austin", "TX"), ("Denver", "CO"), ("Portland", "OR"), ("Seattle", "WA"),
    ("Boston", "MA"), ("Atlanta", "GA"), ("Phoenix", "AZ"), ("Madison", "WI"),
)


def create_customer(index: int, group_size: int, rng: random.Random) -> dict[str, str]:
    group = index // group_size
    city, state = CITIES[group % len(CITIES)]
    first_name = FIRST_NAMES[(index * 7 + group) % len(FIRST_NAMES)]
    last_name = LAST_NAMES[(index * 11 + group * 3) % len(LAST_NAMES)]
    token = "".join(rng.choices("abcdefghjkmnpqrstuvwxyz23456789", k=12))
    return {
        "customer_id": f"C{index + 1:06d}",
        "customer_name": f"{first_name} {last_name}",
        "email": f"{first_name.lower()}.{token}@example.test",
        "phone": f"+1-555-01{rng.randrange(10, 100):02d}-{rng.randrange(1000, 10000):04d}",
        "address": (
            f"{rng.randrange(1, 9999)} "
            f"{rng.choice(STREETS)}"
        ),
        "city": city,
        "state": state,
        "postal_code": f"{10000 + group:05d}",
        "country": "USA",
    }


def vary_customer(customer: dict[str, str], index: int) -> dict[str, str]:
    reference = customer.copy()
    group_type = (index // GROUP_SIZE) % 20

    if group_type == 0:
        return reference

    if 1 <= group_type <= 5:
        field = ("customer_name", "email", "phone", "address")[index % 4]
        if field == "customer_name":
            reference[field] = customer[field][:-1]
        elif field == "email":
            local_part, domain = customer[field].split("@", 1)
            replacement = "x" if local_part[-1] != "x" else "y"
            reference[field] = f"{local_part[:-1]}{replacement}@{domain}"
        elif field == "phone":
            last_digit = int(customer[field][-1])
            reference[field] = f"{customer[field][:-1]}{(last_digit + 1) % 10}"
        else:
            house_number, street = customer[field].split(" ", 1)
            reference[field] = f"{int(house_number) % 9999 + 1} {street}"
        return reference

    if 6 <= group_type <= 12:
        first_name, last_name = customer["customer_name"].split(" ", 1)
        reference["customer_name"] = f"{last_name} {first_name}"
        reference["email"] = f"record{index}@different.test"
        reference["phone"] = f"+1-555-99{index % 100:02d}-{index % 10000:04d}"
        current_street = customer["address"].split(" ", 1)[1]
        alternate_street = next(street for street in STREETS if street != current_street)
        reference["address"] = f"{10000 + index} {alternate_street}"
        return reference

    if 13 <= group_type <= 17:
        reference["customer_name"] = f"Zq{index:04d} Qv"
        reference["email"] = f"unrelated{index}@isolated.invalid"
        reference["phone"] = f"99999999{index:05d}"
        reference["address"] = f"{20000 + index} Unknown Way"
        return reference

    reference["postal_code"] = f"{90000 + index // GROUP_SIZE:05d}"
    return reference


def write_customers(path: Path, customers: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(customers)


def main() -> None:
    rng = random.Random(20261022)
    source_a = [
        create_customer(index, GROUP_SIZE, rng)
        for index in range(ROW_COUNT)
    ]
    source_b = [
        vary_customer(customer, index)
        for index, customer in enumerate(source_a)
    ]
    rng.shuffle(source_b)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    source_a_path = OUTPUT_DIR / "source_a.csv"
    source_b_path = OUTPUT_DIR / "source_b.csv"
    write_customers(source_a_path, source_a)
    write_customers(source_b_path, source_b)
    print(f"Wrote {len(source_a)} rows to {source_a_path}")
    print(f"Wrote {len(source_b)} rows to {source_b_path}")
    print(
        f"Records are grouped in blocks of {GROUP_SIZE} for postal-code "
        f"candidate blocking."
    )


if __name__ == "__main__":
    main()
