// orderbook_bindings.cpp - FIXED VERSION
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <pybind11/operators.h>

#include <iostream>
#include <algorithm>
#include <tuple>
#include <memory>
#include <map>
#include <unordered_map>
#include <list>
#include <vector>
#include <numeric>

enum class OrderType {
    GoodTillCancel,
    FillAndKill
};

enum class Side {
    Buy,
    Sell
};

enum class Token {
    YES,
    NO
};

using Price = std::uint32_t;
using Quantity = std::uint32_t;
using OrderId = std::uint32_t;

struct LevelInfo {
    Price price_;
    Quantity quantity_;
};

using LevelInfos = std::vector<LevelInfo>;

class OrderbookLevelInfos {
public:
    OrderbookLevelInfos(const LevelInfos& bids, const LevelInfos& asks)
        : bids_(bids), asks_(asks) {}

    const LevelInfos& GetAsks() const { return asks_; }
    const LevelInfos& GetBids() const { return bids_; }

private:
    LevelInfos asks_;
    LevelInfos bids_;
};

class Order {
public:
    // Constructor WITH Token (new version)
    Order(OrderType orderType, OrderId orderId, Side side, Price price, Quantity quantity, 
          const std::string& user_id, Token token)
        : orderType_(orderType), orderId_(orderId), side_(side), price_(price), 
          initialQuantity_(quantity), remainingQuantity_(quantity), user_id_(user_id), token_(token) {}

    // Constructor WITHOUT Token (backward compatibility)
    Order(OrderType orderType, OrderId orderId, Side side, Price price, Quantity quantity, 
          const std::string& user_id)
        : orderType_(orderType), orderId_(orderId), side_(side), price_(price), 
          initialQuantity_(quantity), remainingQuantity_(quantity), user_id_(user_id), token_(Token::YES) {}

    OrderId GetOrderId() const { return orderId_; }
    OrderType GetOrderType() const { return orderType_; }
    Side GetSide() const { return side_; }
    Price GetPrice() const { return price_; }
    Quantity GetRemainingQuantity() const { return remainingQuantity_; }
    Quantity GetInitialQuantity() const { return initialQuantity_; }
    std::string GetUserId() const { return user_id_; }
    Token GetToken() const { return token_; }
    bool IsFilled() const { return GetRemainingQuantity() == 0; }

    void Fill(Quantity quantity) {
        if (quantity > GetRemainingQuantity())
            throw std::logic_error("Order cannot be filled for order " + std::to_string(GetOrderId()));
        remainingQuantity_ -= quantity;
    }
    
private:
    OrderType orderType_;        
    OrderId orderId_;           
    Side side_;
    Price price_;
    Quantity initialQuantity_;    
    Quantity remainingQuantity_;
    std::string user_id_;
    Token token_;
};

using OrderPointer = std::shared_ptr<Order>;
using OrderPointers = std::list<OrderPointer>;

struct TradeInfo {
    OrderId orderId_;
    Price price_;
    Quantity quantity_;
};

class Trade {
public:
    Trade(const TradeInfo& bidTrade, const TradeInfo& askTrade)
        : bidTrade_(bidTrade), askTrade_(askTrade) {}
    
    const TradeInfo& GetBidTrade() const { return bidTrade_; }
    const TradeInfo& GetAskTrade() const { return askTrade_; }

private:
    TradeInfo bidTrade_;
    TradeInfo askTrade_;
};

using Trades = std::vector<Trade>;

class Orderbook {
private:
    struct OrderEntry {
        OrderPointer order_{ nullptr };
        OrderPointers::iterator location_;
    };
    
    std::map<Price, OrderPointers, std::greater<Price>> bids_;
    std::map<Price, OrderPointers, std::less<Price>> asks_;
    std::unordered_map<OrderId, OrderEntry> orders_;
    OrderId next_order_id_ = 1;

public:
    // Method WITH Token (new version)
    Trades AddOrder(OrderType orderType, Side side, Price price, Quantity quantity, 
                   const std::string& user_id, Token token) {
        auto order = std::make_shared<Order>(orderType, next_order_id_++, side, price, quantity, user_id, token);
        return AddOrderInternal(order);
    }

    // Method WITHOUT Token (backward compatibility)
    Trades AddOrder(OrderType orderType, Side side, Price price, Quantity quantity, const std::string& user_id) {
        auto order = std::make_shared<Order>(orderType, next_order_id_++, side, price, quantity, user_id);
        return AddOrderInternal(order);
    }
    
    Trades AddOrderInternal(OrderPointer order) {
        if (orders_.find(order->GetOrderId()) != orders_.end())
            return {};

        if (order->GetOrderType() == OrderType::FillAndKill &&
            !CanMatch(order->GetSide(), order->GetPrice()))
            return {};

        OrderPointers::iterator iterator;
        if (order->GetSide() == Side::Buy) {
            auto& orderList = bids_[order->GetPrice()];
            orderList.push_back(order);
            iterator = std::prev(orderList.end());
        } else {
            auto& orderList = asks_[order->GetPrice()];
            orderList.push_back(order);
            iterator = std::prev(orderList.end());
        }

        orders_.insert({order->GetOrderId(), OrderEntry{order, iterator}});
        return MatchOrders();
    }

    bool CanMatch(Side side, Price price) const {
        if (side == Side::Buy) {
            if (asks_.empty())
                return false;
            const auto& [bestAsk, _] = *asks_.begin();
            return price >= bestAsk;
        } else {
            if (bids_.empty())
                return false;
            const auto& [bestBid, _] = *bids_.begin();
            return price <= bestBid;
        }
    }

    Trades MatchOrders() {
        Trades trades;
        trades.reserve(orders_.size());

        while (true) {
            if (bids_.empty() || asks_.empty())
                break;
            
            auto& [bidPrice, bidOrders] = *bids_.begin();
            auto& [askPrice, askOrders] = *asks_.begin();

            if (bidPrice < askPrice)
                break;

            while (!bidOrders.empty() && !askOrders.empty()) {
                auto& bid = bidOrders.front();
                auto& ask = askOrders.front();

                Quantity quantity = std::min(bid->GetRemainingQuantity(), ask->GetRemainingQuantity());
                bid->Fill(quantity);
                ask->Fill(quantity);

                if (bid->IsFilled()) {
                    bidOrders.pop_front();
                    orders_.erase(bid->GetOrderId());
                }
                if (ask->IsFilled()) {
                    askOrders.pop_front();
                    orders_.erase(ask->GetOrderId());
                }

                trades.push_back(Trade{
                    TradeInfo{bid->GetOrderId(), ask->GetPrice(), quantity},
                    TradeInfo{ask->GetOrderId(), ask->GetPrice(), quantity}
                });
            }

            if (bidOrders.empty())
                bids_.erase(bidPrice);
            if (askOrders.empty())
                asks_.erase(askPrice);
        }

        return trades;
    }

    void CancelOrder(OrderId orderId) {
        auto it = orders_.find(orderId);
        if (it == orders_.end())
            return;

        const auto& [order, orderIterator] = it->second;
        orders_.erase(orderId);

        auto price = order->GetPrice();
        if (order->GetSide() == Side::Sell) {
            auto& orderList = asks_.at(price);
            orderList.erase(orderIterator);
            if (orderList.empty())
                asks_.erase(price);
        } else {
            auto& orderList = bids_.at(price);
            orderList.erase(orderIterator);
            if (orderList.empty())
                bids_.erase(price);
        }
    }

    std::size_t Size() const { return orders_.size(); }

    OrderbookLevelInfos GetOrderInfos() const {
        LevelInfos bidInfos, askInfos;
        bidInfos.reserve(bids_.size());
        askInfos.reserve(asks_.size());

        auto CreateLevelInfos = [](Price price, const OrderPointers& orders) {
            return LevelInfo{price, std::accumulate(orders.begin(), orders.end(), (Quantity)0,
                [](Quantity runningSum, const OrderPointer& order) {
                    return runningSum + order->GetRemainingQuantity();
                })};
        };

        for (const auto& [price, orders] : bids_)
            bidInfos.push_back(CreateLevelInfos(price, orders));
        for (const auto& [price, orders] : asks_)
            askInfos.push_back(CreateLevelInfos(price, orders));
        
        return OrderbookLevelInfos{bidInfos, askInfos};
    }
};

namespace py = pybind11;

PYBIND11_MODULE(orderbook_cpp, m) {
    m.doc() = "C++ Orderbook for Prediction Markets";

    // Enums
    py::enum_<OrderType>(m, "OrderType")
        .value("GoodTillCancel", OrderType::GoodTillCancel)
        .value("FillAndKill", OrderType::FillAndKill);

    py::enum_<Side>(m, "Side")
        .value("Buy", Side::Buy)
        .value("Sell", Side::Sell);

    py::enum_<Token>(m, "Token")
        .value("YES", Token::YES)
        .value("NO", Token::NO);

    // LevelInfo
    py::class_<LevelInfo>(m, "LevelInfo")
        .def(py::init<Price, Quantity>())
        .def_readwrite("price", &LevelInfo::price_)
        .def_readwrite("quantity", &LevelInfo::quantity_);

    // OrderbookLevelInfos
    py::class_<OrderbookLevelInfos>(m, "OrderbookLevelInfos")
        .def(py::init<const LevelInfos&, const LevelInfos&>())
        .def("get_asks", &OrderbookLevelInfos::GetAsks)
        .def("get_bids", &OrderbookLevelInfos::GetBids);

    // TradeInfo
    py::class_<TradeInfo>(m, "TradeInfo")
        .def(py::init<OrderId, Price, Quantity>())
        .def_readwrite("order_id", &TradeInfo::orderId_)
        .def_readwrite("price", &TradeInfo::price_)
        .def_readwrite("quantity", &TradeInfo::quantity_);

    // Trade
    py::class_<Trade>(m, "Trade")
        .def(py::init<const TradeInfo&, const TradeInfo&>())
        .def("get_bid_trade", &Trade::GetBidTrade)
        .def("get_ask_trade", &Trade::GetAskTrade);

    // Order
    py::class_<Order>(m, "Order")
        .def("get_order_id", &Order::GetOrderId)
        .def("get_order_type", &Order::GetOrderType)
        .def("get_side", &Order::GetSide)
        .def("get_price", &Order::GetPrice)
        .def("get_remaining_quantity", &Order::GetRemainingQuantity)
        .def("get_initial_quantity", &Order::GetInitialQuantity)
        .def("get_user_id", &Order::GetUserId)
        .def("get_token", &Order::GetToken)
        .def("is_filled", &Order::IsFilled);

    // Orderbook - Main class
    py::class_<Orderbook>(m, "Orderbook")
        .def(py::init<>())
        .def("add_order", py::overload_cast<OrderType, Side, Price, Quantity, const std::string&, Token>(&Orderbook::AddOrder), 
             "Add an order with token to the orderbook")
        .def("add_order", py::overload_cast<OrderType, Side, Price, Quantity, const std::string&>(&Orderbook::AddOrder), 
             "Add an order to the orderbook (defaults to YES token)")
        .def("cancel_order", &Orderbook::CancelOrder, "Cancel an order")
        .def("size", &Orderbook::Size, "Get number of orders")
        .def("get_order_infos", &Orderbook::GetOrderInfos, "Get market data");
}