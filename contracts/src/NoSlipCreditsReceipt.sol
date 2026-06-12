// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";

/// @title Optional purchase receipt for off-chain NoSlip Credits
/// @notice This contract records purchase proof only. It is not a financial
/// instrument and does not represent ownership, yield, or an investment return.
contract NoSlipCreditsReceipt is Ownable {
    uint256 public nextReceiptId = 1;
    uint256 public creditPriceInWei = 2 * 10**13; // 1 Credit = 0.00002 ETH

    event CreditsPurchased(
        address indexed user,
        uint256 indexed receiptId,
        uint256 creditAmount,
        uint256 amountPaid,
        string asset,
        uint256 timestamp
    );
    event CreditPriceUpdated(uint256 newPrice);

    constructor(address initialOwner) Ownable(initialOwner) {}

    function setCreditPrice(uint256 newPrice) external onlyOwner {
        creditPriceInWei = newPrice;
        emit CreditPriceUpdated(newPrice);
    }

    function recordPurchase(
        address user,
        uint256 creditAmount,
        uint256 amountPaid,
        string calldata asset
    ) external onlyOwner returns (uint256 receiptId) {
        require(user != address(0), "NoSlip: zero user");
        require(creditAmount > 0, "NoSlip: zero credits");
        require(amountPaid > 0, "NoSlip: zero payment");
        require(bytes(asset).length > 0, "NoSlip: empty asset");

        receiptId = nextReceiptId++;
        emit CreditsPurchased(
            user,
            receiptId,
            creditAmount,
            amountPaid,
            asset,
            block.timestamp
        );
    }

    function purchaseCredits(uint256 creditAmount) external payable returns (uint256 receiptId) {
        require(creditAmount > 0, "NoSlip: zero credits");
        uint256 requiredPayment = creditAmount * creditPriceInWei;
        require(msg.value >= requiredPayment, "NoSlip: insufficient payment");

        receiptId = nextReceiptId++;
        emit CreditsPurchased(
            msg.sender,
            receiptId,
            creditAmount,
            requiredPayment,
            "ETH",
            block.timestamp
        );

        if (msg.value > requiredPayment) {
            payable(msg.sender).transfer(msg.value - requiredPayment);
        }
    }

    function withdraw() external onlyOwner {
        uint256 balance = address(this).balance;
        require(balance > 0, "NoSlip: zero balance");
        payable(owner()).transfer(balance);
    }
}
