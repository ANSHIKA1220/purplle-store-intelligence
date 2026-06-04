import pandas as pd

from app.database import SessionLocal
from app.models.pos_transaction import POSTransaction


CSV_PATH = r"data\POS - sample transactionsb1e826f.csv"


def load_transactions():

    df = pd.read_csv(CSV_PATH)

    db = SessionLocal()

    count = 0

    for _, row in df.iterrows():
        date_str = str(row["order_date"]).strip()
        time_str = str(row["order_time"]).strip()

        timestamp = pd.to_datetime(
            f"{date_str} {time_str}",
            dayfirst=True
        ).to_pydatetime()

        transaction = POSTransaction(
            order_id=int(row["order_id"]),
            store_id=row["store_id"],
            product_id=int(row["product_id"]),
            brand_name=row["brand_name"],
            order_timestamp=timestamp,
            total_amount=float(row["total_amount"])
        )

        db.add(transaction)

        count += 1

    db.commit()

    db.close()

    print(f"{count} transactions loaded")


if __name__ == "__main__":
    load_transactions()