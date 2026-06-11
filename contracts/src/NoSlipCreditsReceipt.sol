// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";

/// @title Optional purchase receipt for off-chain NoSlip Credits
/// @notice This contract records purchase proof only. It is not a financial
/// instrument and does not represent ownership, yield, or an investment return.
contract NoSlipCreditsReceipt is Ownable {
    uint256 public nextReceiptId = 1;

    event CreditsPurchased(
        address indexed user,
        uint256 indexed receiptId,
        uint256 creditAmount,
        uint256 amountPaid,
        string asset,
        uint256 timestamp
    );

    constructor(address initialOwner) Ownable(initialOwner) {}

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
}
