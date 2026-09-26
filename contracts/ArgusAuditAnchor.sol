// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title ArgusAuditAnchor
 * @dev Immutably anchors cryptographic SHA-256 hashes of critical ARGUS procurement audit events.
 * Storing only (bytes32 eventId, bytes32 eventHash) guarantees zero leak of confidential procurement facts,
 * tender clauses, or bidder PII onto the public blockchain.
 */
contract ArgusAuditAnchor {
    struct Anchor {
        bytes32 eventHash;
        uint64 blockTimestamp;
        address submitter;
    }

    address public owner;
    mapping(address => bool) public authorizedRelayers;
    mapping(bytes32 => Anchor) private _anchors;

    event AuditAnchored(
        bytes32 indexed eventId,
        bytes32 eventHash,
        address indexed submitter,
        uint256 timestamp
    );

    event RelayerStatusUpdated(address indexed relayer, bool authorized);
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

    error NotOwner();
    error NotAuthorizedRelayer();
    error EventAlreadyAnchored(bytes32 eventId);
    error InvalidEventId();
    error InvalidEventHash();
    error InvalidAddress();

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    modifier onlyRelayer() {
        if (!authorizedRelayers[msg.sender] && msg.sender != owner) {
            revert NotAuthorizedRelayer();
        }
        _;
    }

    constructor() {
        owner = msg.sender;
        authorizedRelayers[msg.sender] = true;
        emit OwnershipTransferred(address(0), msg.sender);
        emit RelayerStatusUpdated(msg.sender, true);
    }

    function transferOwnership(address newOwner) external onlyOwner {
        if (newOwner == address(0)) revert InvalidAddress();
        address prev = owner;
        owner = newOwner;
        emit OwnershipTransferred(prev, newOwner);
    }

    function setRelayer(address relayer, bool authorized) external onlyOwner {
        if (relayer == address(0)) revert InvalidAddress();
        authorizedRelayers[relayer] = authorized;
        emit RelayerStatusUpdated(relayer, authorized);
    }

    function anchorAuditEvent(bytes32 eventId, bytes32 eventHash) external onlyRelayer {
        if (eventId == bytes32(0)) revert InvalidEventId();
        if (eventHash == bytes32(0)) revert InvalidEventHash();
        if (_anchors[eventId].eventHash != bytes32(0)) {
            revert EventAlreadyAnchored(eventId);
        }

        _anchors[eventId] = Anchor({
            eventHash: eventHash,
            blockTimestamp: uint64(block.timestamp),
            submitter: msg.sender
        });

        emit AuditAnchored(eventId, eventHash, msg.sender, block.timestamp);
    }

    function getAuditAnchor(bytes32 eventId) external view returns (
        bytes32 eventHash,
        uint64 blockTimestamp,
        address submitter
    ) {
        Anchor memory a = _anchors[eventId];
        return (a.eventHash, a.blockTimestamp, a.submitter);
    }

    function verifyAuditEvent(bytes32 eventId, bytes32 eventHash) external view returns (bool) {
        if (eventId == bytes32(0) || eventHash == bytes32(0)) return false;
        return _anchors[eventId].eventHash == eventHash;
    }
}
