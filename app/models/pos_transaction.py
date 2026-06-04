from sqlalchemy import Column
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Float
from sqlalchemy import DateTime

from app.database import Base


class POSTransaction(Base):

    __tablename__ = "pos_transactions"

    order_id = Column(Integer, primary_key=True)

    store_id = Column(String)

    product_id = Column(Integer)

    brand_name = Column(String)

    order_timestamp = Column(DateTime)

    total_amount = Column(Float)