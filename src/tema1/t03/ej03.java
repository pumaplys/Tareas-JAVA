package tema1.t03;

import java.util.Scanner;

public class ej03 {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);
        System.out.println("Digite su edad:");
        int edad = sc.nextInt();

        System.out.println(((edad >= 18) ? "Eres mayor de edad" : "Eres menor de edad"));
    }
}
