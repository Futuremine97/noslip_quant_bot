import { buildModule } from "@nomicfoundation/hardhat-ignition/modules";
import { parseEther } from "viem";

export default buildModule("NoSlipModule", (module) => {
  const owner = module.getAccount(0);
  const creditsReceipt = module.contract("NoSlipCreditsReceipt", [owner]);
  const accessPass = module.contract("NoSlipAccessPass", [owner]);
  const nsqToken = module.contract("NSQToken", [
    parseEther("100000000"),
    parseEther("1000000000"),
    owner,
  ]);

  return {
    creditsReceipt,
    accessPass,
    nsqToken,
  };
});
