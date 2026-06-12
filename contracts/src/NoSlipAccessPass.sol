// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {ERC721} from "@openzeppelin/contracts/token/ERC721/ERC721.sol";
import {ERC721Pausable} from "@openzeppelin/contracts/token/ERC721/extensions/ERC721Pausable.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";

/// @title Non-transferable NoSlip product access pass
/// @notice Tiers represent product access only and do not imply investment value.
contract NoSlipAccessPass is ERC721Pausable, Ownable {
    enum Tier {
        FREE,
        PRO,
        RESEARCH,
        CREATOR
    }

    uint256 public nextTokenId = 1;
    mapping(uint256 tokenId => Tier tier) public tierOf;
    mapping(Tier => uint256) public tierPrices;

    event AccessTierUpdated(uint256 indexed tokenId, Tier tier);
    event TierPriceUpdated(Tier indexed tier, uint256 price);

    constructor(address initialOwner)
        ERC721("NoSlip Access Pass", "NSQPASS")
        Ownable(initialOwner)
    {
        tierPrices[Tier.FREE] = 0;
        tierPrices[Tier.PRO] = 0.01 ether;
        tierPrices[Tier.RESEARCH] = 0.05 ether;
        tierPrices[Tier.CREATOR] = 0.1 ether;
    }

    function setTierPrice(Tier tier, uint256 price) external onlyOwner {
        tierPrices[tier] = price;
        emit TierPriceUpdated(tier, price);
    }

    function mint(address account, Tier tier)
        external
        onlyOwner
        returns (uint256 tokenId)
    {
        require(account != address(0), "NoSlip: zero account");
        tokenId = nextTokenId++;
        tierOf[tokenId] = tier;
        _safeMint(account, tokenId);
        emit AccessTierUpdated(tokenId, tier);
    }

    function publicPurchasePass(Tier tier) external payable returns (uint256 tokenId) {
        uint256 requiredPrice = tierPrices[tier];
        require(msg.value >= requiredPrice, "NoSlip: insufficient payment");

        tokenId = nextTokenId++;
        tierOf[tokenId] = tier;
        _safeMint(msg.sender, tokenId);
        emit AccessTierUpdated(tokenId, tier);

        if (msg.value > requiredPrice) {
            payable(msg.sender).transfer(msg.value - requiredPrice);
        }
    }

    function setTier(uint256 tokenId, Tier tier) external onlyOwner {
        _requireOwned(tokenId);
        tierOf[tokenId] = tier;
        emit AccessTierUpdated(tokenId, tier);
    }

    function burn(uint256 tokenId) external onlyOwner {
        _burn(tokenId);
        delete tierOf[tokenId];
    }

    function withdraw() external onlyOwner {
        uint256 balance = address(this).balance;
        require(balance > 0, "NoSlip: zero balance");
        payable(owner()).transfer(balance);
    }

    function pause() external onlyOwner {
        _pause();
    }

    function unpause() external onlyOwner {
        _unpause();
    }

    function _update(address to, uint256 tokenId, address auth)
        internal
        override(ERC721Pausable)
        returns (address)
    {
        address from = _ownerOf(tokenId);
        require(
            from == address(0) || to == address(0),
            "NoSlip: access pass is non-transferable"
        );
        return super._update(to, tokenId, auth);
    }
}
