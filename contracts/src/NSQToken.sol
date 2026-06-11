// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {ERC20Capped} from "@openzeppelin/contracts/token/ERC20/extensions/ERC20Capped.sol";
import {ERC20Pausable} from "@openzeppelin/contracts/token/ERC20/extensions/ERC20Pausable.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";

/// @title NoSlip Quant Token utility-token placeholder
/// @notice NSQ is designed only for future analytics/API access and creator
/// reputation features. It provides no yield, profit sharing, guaranteed return,
/// or automatic trading-profit distribution. Legal review is required before
/// any deployment or public distribution.
contract NSQToken is ERC20Capped, ERC20Pausable, Ownable {
    constructor(
        uint256 initialSupply,
        uint256 maximumSupply,
        address initialOwner
    )
        ERC20("NoSlip Quant Token", "NSQ")
        ERC20Capped(maximumSupply)
        Ownable(initialOwner)
    {
        require(initialSupply <= maximumSupply, "NoSlip: supply exceeds cap");
        _mint(initialOwner, initialSupply);
    }

    function pause() external onlyOwner {
        _pause();
    }

    function unpause() external onlyOwner {
        _unpause();
    }

    function _update(address from, address to, uint256 value)
        internal
        override(ERC20Capped, ERC20Pausable)
    {
        super._update(from, to, value);
    }
}
